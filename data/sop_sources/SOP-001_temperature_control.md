# SOP-001: Cargo Temperature Control Limits

**Document ID:** SOP-001
**Owner:** Quality Assurance (QA) Department
**Applies to:** All reefer (refrigerated) trucks and containers

## 1. Purpose
This SOP defines the allowed temperature range for each cargo class. The IoT sensor reading (`iot_temperature`, in °C) on each vehicle must be compared against these limits.

## 2. Temperature Ranges by Cargo Class

| Cargo class | Examples | Allowed range |
|---|---|---|
| Frozen goods | Ice cream, frozen meat, frozen seafood | -10°C to -5°C |
| Fresh perishables (chilled) | Fresh produce, dairy, fresh fish, flowers | 2°C to 8°C |
| Pharmaceuticals and vaccines | Vaccines, insulin, biologics | 2°C to 8°C (strict) |
| Ambient controlled goods | Chocolate, some medicines, wine | 15°C to 25°C |

## 3. Fresh Perishables Rule
- Fresh perishables must stay between **2°C and 8°C**.
- A reading **above 8°C** is a **temperature excursion**.
- A reading **below 2°C** is a **freeze risk** (produce can be damaged by freezing).
- An excursion above 8°C lasting more than **30 minutes**, or any single reading above **12°C**, is a **Tier 1 breach** (see SOP-002).
- A reading between 8°C and 12°C lasting less than 30 minutes is a **Tier 2 warning**.

## 4. Frozen Goods Rule
- Frozen goods must stay at or below **-5°C**.
- A reading between -5°C and 0°C is a **Tier 2 warning**.
- A reading **above 0°C** means thawing has started and is a **Tier 1 breach**.

## 5. Monitoring Frequency
- Sensors report at least once every hour.
- Dispatchers must review the active fleet temperature dashboard at the start of each shift and after every alert.
- If a sensor stops reporting for more than 2 hours, treat it as a **Tier 2 warning** and contact the driver to read the manual thermometer.
