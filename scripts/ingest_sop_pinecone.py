"""
Phase 1 - SOP ingestion.

Loads the cold-chain SOP documents (PDF, Word .docx or Markdown, from a local
folder or an S3 bucket), converts them to Markdown, splits them into
section-level chunks, embeds them with a local
sentence-transformer model and upserts them into a Pinecone serverless index
so the agent can retrieve policy at query time.

Usage:
    python scripts/ingest_sop_pinecone.py              # source from SOP_SOURCE in .env
    python scripts/ingest_sop_pinecone.py --source s3  # override the source
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from dotenv import load_dotenv
from langchain_core.documents import Document
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_pinecone import PineconeVectorStore
from langchain_text_splitters import MarkdownHeaderTextSplitter, RecursiveCharacterTextSplitter
from pinecone import Pinecone, ServerlessSpec
from exception import ConfigError
from sop_parsers import SUPPORTED_EXTENSIONS, UnsupportedFormatError, to_markdown
# __file__ is 'scripts/ingest_sop_pinecone.py'
PROJECT_ROOT = Path(__file__).resolve().parents[1]

logger = logging.getLogger("sop_ingestion")

# Also pick up old .doc files, so the user gets a clear "save as .docx" warning instead of silence
SOP_FILE_EXTENSIONS = SUPPORTED_EXTENSIONS + (".doc",)
SOP_ID_PATTERN = re.compile(r"\bSOP-\d+\b")


@dataclass(frozen=True)
class SOPIngestionConfig:
    """All settings for the SOP ingestion pipeline."""

    pinecone_api_key: str
    index_name: str = "cold-chain-sops"

    # Where the SOP files come from: "local" or "s3"
    sop_source: str = "local"
    sop_dir: Path = PROJECT_ROOT / "data" / "sops"
    s3_bucket: str | None = None
    s3_prefix: str = "sops/"
    aws_region: str | None = None

    # Free local embedding model: turns text into a vector of 384 numbers
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    embedding_dim: int = 384

    cloud: str = "aws"
    region: str = "us-east-1"
    metric: str = "cosine"

    # Safety net for very long sections
    chunk_size: int = 1000
    chunk_overlap: int = 100

    test_questions: tuple[str, ...] = field(default=(
        "What is the allowed temperature for fresh perishables?",
        "What is the difference between a Tier 1 and Tier 2 escalation?",
        "What should I do if a vaccine shipment goes above 8 degrees?",
    ))

    @classmethod
    def from_env(cls, env_file: Path = PROJECT_ROOT / ".env",
                 source_override: str | None = None) -> SOPIngestionConfig:
        """Build the config from the .env file and environment variables."""
        load_dotenv(env_file)

        api_key = os.getenv("PINECONE_API_KEY")
        if not api_key:
            raise ConfigError("PINECONE_API_KEY is missing. Add it to the .env file.")

        source = (source_override or os.getenv("SOP_SOURCE", cls.sop_source)).lower()
        if source not in ("local", "s3"):
            raise ConfigError(f"SOP_SOURCE must be 'local' or 's3', got '{source}'.")

        bucket = os.getenv("SOP_S3_BUCKET")
        if source == "s3" and not bucket:
            raise ConfigError("SOP_SOURCE is 's3' but SOP_S3_BUCKET is missing. Add it to the .env file.")

        # AWS credentials are not stored here: boto3 reads them itself from
        # AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY, ~/.aws/credentials or an EC2 IAM role
        return cls(
            pinecone_api_key=api_key,
            index_name=os.getenv("PINECONE_INDEX_NAME", cls.index_name),
            sop_source=source,
            s3_bucket=bucket,
            s3_prefix=os.getenv("SOP_S3_PREFIX", cls.s3_prefix),
            aws_region=os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION"),
        )


@dataclass(frozen=True)
class IngestionResult:
    """Summary of a pipeline run."""

    files_processed: int
    chunks_uploaded: int
    index_name: str


# ---------- SOP sources ----------

@dataclass(frozen=True)
class SOPDocument:
    """One SOP file, independent of where it was loaded from."""

    name: str      # file name, e.g. 'SOP-002_escalation_tiers.pdf'
    location: str  # full path or s3:// URI, kept as metadata for traceability
    data: bytes    # raw file content; parsed by sop_parsers.to_markdown()


class SOPSource(Protocol):
    """Anything that can return the SOP documents."""

    def load(self) -> list[SOPDocument]: ...


def _is_sop_file(name: str) -> bool:
    return name.lower().endswith(SOP_FILE_EXTENSIONS)


class LocalSOPSource:
    """Reads SOP files (PDF, DOCX, MD) from a folder on disk."""

    def __init__(self, sop_dir: Path) -> None:
        self.sop_dir = sop_dir

    def load(self) -> list[SOPDocument]:
        files = sorted(f for f in self.sop_dir.iterdir() if f.is_file() and _is_sop_file(f.name))
        if not files:
            raise FileNotFoundError(f"No SOP files {SUPPORTED_EXTENSIONS} found in {self.sop_dir}")
        logger.info("Found %d SOP files in %s", len(files), self.sop_dir)
        return [SOPDocument(f.name, str(f), f.read_bytes()) for f in files]


class S3SOPSource:
    """Reads SOP files (PDF, DOCX, MD) from an S3 bucket under a key prefix."""

    def __init__(self, bucket: str, prefix: str = "", region: str | None = None) -> None:
        self.bucket = bucket
        self.prefix = prefix
        self._s3 = boto3.client("s3", region_name=region)

    def load(self) -> list[SOPDocument]:
        uri = f"s3://{self.bucket}/{self.prefix}"
        keys = self._list_keys()
        if not keys:
            raise FileNotFoundError(f"No SOP files {SUPPORTED_EXTENSIONS} found in {uri}")
        logger.info("Found %d SOP files in %s", len(keys), uri)

        return [
            SOPDocument(
                name=key.rsplit("/", 1)[-1],
                location=f"s3://{self.bucket}/{key}",
                data=self._s3.get_object(Bucket=self.bucket, Key=key)["Body"].read(),
            )
            for key in keys
        ]

    def _list_keys(self) -> list[str]:
        """List all SOP object keys under the prefix (handles more than 1000 objects)."""
        paginator = self._s3.get_paginator("list_objects_v2")
        keys = [
            obj["Key"]
            for page in paginator.paginate(Bucket=self.bucket, Prefix=self.prefix)
            for obj in page.get("Contents", [])
            if _is_sop_file(obj["Key"])
        ]
        return sorted(keys)


def build_source(config: SOPIngestionConfig) -> SOPSource:
    """Pick the SOP source named in the config."""
    if config.sop_source == "s3":
        return S3SOPSource(config.s3_bucket, config.s3_prefix, config.aws_region)
    return LocalSOPSource(config.sop_dir)


# ---------- Pipeline ----------

class SOPIngestionPipeline:
    """Load -> chunk -> embed -> upsert -> verify SOP documents in Pinecone."""

    def __init__(self, config: SOPIngestionConfig, source: SOPSource | None = None) -> None:
        self.config = config
        self.source = source or build_source(config)
        self._embeddings: HuggingFaceEmbeddings | None = None
        self._pinecone = Pinecone(api_key=config.pinecone_api_key)

        self._header_splitter = MarkdownHeaderTextSplitter(
            headers_to_split_on=[("#", "sop_title"), ("##", "section")],
            strip_headers=False,
        )
        self._size_splitter = RecursiveCharacterTextSplitter(
            chunk_size=config.chunk_size,
            chunk_overlap=config.chunk_overlap,
        )

    @property
    def embeddings(self) -> HuggingFaceEmbeddings:
        """Load the embedding model only when first needed (~90 MB download on first run)."""
        if self._embeddings is None:
            logger.info("Loading embedding model %s", self.config.embedding_model)
            self._embeddings = HuggingFaceEmbeddings(model_name=self.config.embedding_model)
        return self._embeddings

    # ---------- Pipeline steps ----------

    def load_documents(self) -> list[SOPDocument]:
        """Load all SOP documents from the configured source."""
        return self.source.load()

    def chunk_document(self, doc: SOPDocument) -> tuple[list[Document], list[str]]:
        """Convert one SOP to Markdown and split it into section-level chunks with stable IDs."""
        text = to_markdown(doc.name, doc.data)
        sop_id = self._sop_id(doc.name, text)
        sections = self._size_splitter.split_documents(self._header_splitter.split_text(text))

        ids = []
        for i, chunk in enumerate(sections):
            title = chunk.metadata.get("sop_title", sop_id)
            # Prefix the SOP title so every chunk makes sense on its own
            if not chunk.page_content.startswith("# "):
                chunk.page_content = f"{title}\n\n{chunk.page_content}"
            chunk.metadata.update({"sop_id": sop_id, "source": doc.name, "location": doc.location})
            # Stable IDs: re-running overwrites chunks instead of duplicating them
            ids.append(f"{sop_id}-{i}")

        logger.info("  %s: %d chunks", doc.name, len(sections))
        return sections, ids

    def ensure_index(self):
        """Create the Pinecone index if it does not exist and return a handle to it."""
        name = self.config.index_name
        if not self._pinecone.has_index(name):
            logger.info("Creating Pinecone index '%s'", name)
            self._pinecone.create_index(
                name=name,
                dimension=self.config.embedding_dim,
                metric=self.config.metric,
                spec=ServerlessSpec(cloud=self.config.cloud, region=self.config.region),
            )
        return self._pinecone.Index(name)

    def upsert(self, index, chunks: list[Document], ids: list[str]) -> PineconeVectorStore:
        """Embed the chunks, upsert them, and remove chunks left over from older versions."""
        logger.info("Uploading %d chunks to '%s'", len(chunks), self.config.index_name)
        store = PineconeVectorStore(index=index, embedding=self.embeddings)
        store.add_documents(chunks, ids=ids)
        self._delete_stale_chunks(index, keep_ids=set(ids))
        self._wait_until_searchable(index, expected=len(chunks))
        return store

    def verify(self, store: PineconeVectorStore) -> None:
        """Run the test questions and log the best-matching SOP section for each."""
        logger.info("Retrieval check:")
        for question in self.config.test_questions:
            doc, score = store.similarity_search_with_score(question, k=1)[0]
            logger.info("  Q: %s", question)
            logger.info("     -> %s | %s (score %.2f)",
                        doc.metadata["sop_id"], doc.metadata.get("section", ""), score)

    def run(self, verify: bool = True) -> IngestionResult:
        """Run the full pipeline end to end."""
        documents = self.load_documents()

        chunks: list[Document] = []
        ids: list[str] = []
        processed = 0
        for doc in documents:
            try:
                doc_chunks, doc_ids = self.chunk_document(doc)
            except UnsupportedFormatError as exc:
                # One bad file should not stop the other SOPs from being ingested
                logger.warning("Skipping %s", exc)
                continue
            chunks.extend(doc_chunks)
            ids.extend(doc_ids)
            processed += 1

        if not chunks:
            raise FileNotFoundError("No SOP files could be parsed, nothing to upload.")

        index = self.ensure_index()
        store = self.upsert(index, chunks, ids)

        if verify:
            self.verify(store)

        return IngestionResult(
            files_processed=processed,
            chunks_uploaded=len(chunks),
            index_name=self.config.index_name,
        )

    # ---------- Helpers ----------

    @staticmethod
    def _sop_id(file_name: str, text: str) -> str:
        """Prefer the SOP number written in the document title, else use the file name."""
        match = SOP_ID_PATTERN.search(text[:500]) or SOP_ID_PATTERN.search(file_name)
        return match.group(0) if match else Path(file_name).stem

    @staticmethod
    def _delete_stale_chunks(index, keep_ids: set[str]) -> None:
        """Delete chunks of the same SOPs that no longer exist (e.g. an SOP got shorter)."""
        sop_prefixes = {chunk_id.rsplit("-", 1)[0] + "-" for chunk_id in keep_ids}
        stale = [
            vector_id
            for prefix in sop_prefixes
            for batch in index.list(prefix=prefix)
            for vector_id in batch
            if vector_id not in keep_ids
        ]
        if stale:
            index.delete(ids=stale)
            logger.info("Removed %d outdated chunks", len(stale))

    @staticmethod
    def _wait_until_searchable(index, expected: int, timeout_s: int = 30) -> None:
        """Pinecone takes a few seconds before new vectors become searchable."""
        for _ in range(timeout_s):
            if index.describe_index_stats().total_vector_count >= expected:
                return
            time.sleep(1)
        logger.warning("Index not fully ready after %ds; search results may be incomplete", timeout_s)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Ingest SOP documents into Pinecone.")
    parser.add_argument("--source", choices=["local", "s3"],
                        help="Where to read SOPs from (default: SOP_SOURCE in .env, else 'local').")
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
    # Keep third-party libraries quiet so only pipeline progress is shown
    for noisy in ("httpx", "sentence_transformers", "huggingface_hub", "botocore", "boto3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    try:
        config = SOPIngestionConfig.from_env(source_override=args.source)
        result = SOPIngestionPipeline(config).run()
    except (ConfigError, FileNotFoundError) as exc:
        logger.error(exc)
        return 1
    except (BotoCoreError, ClientError) as exc:
        # e.g. missing AWS credentials, wrong bucket name, or no permission
        logger.error("Could not read SOPs from S3: %s", exc)
        return 1

    logger.info("✅ SOP ingestion complete: %d files, %d chunks -> '%s'",
                result.files_processed, result.chunks_uploaded, result.index_name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
