# SOP-004: Delay and Route Risk Management

**Document ID:** SOP-004
**Owner:** Dispatch Operations
**Applies to:** All dispatchers

## 1. Purpose
This SOP explains how to act on delay and route risk indicators from the fleet system.

## 2. Key Indicators
| Indicator | System field | Range | Meaning |
|---|---|---|---|
| Delay probability | `delay_probability` | 0 to 1 | Chance the shipment will be late |
| Route risk level | `route_risk_level` | 0 to 10 | Danger level of the current route |
| Port congestion | `port_congestion_level` | 0 to 10 | How busy the destination port is |
| Risk classification | `risk_classification` | Low / Moderate / High Risk | Overall shipment risk |

## 3. Action Thresholds
- **Delay probability above 0.7** on a perishable or vaccine shipment: **Tier 2 warning**. Inform the customer of a possible delay and check whether the cargo's temperature can be held for the extra time.
- **Route risk level above 8**: consider rerouting. Contact the driver to confirm road conditions.
- **Port congestion level above 8**: check with the port agent for the expected waiting time. Reefer cargo waiting more than 6 hours at a port needs a power plug-in confirmation.
- **High Risk** classification together with a temperature excursion: escalate as **Tier 1**.

## 4. Rerouting Rules
- Rerouting must be approved by the **Dispatch Shift Lead**.
- A new route must not add more than **20%** to the remaining travel time for perishables.
- Always inform the driver and the customer after a reroute.
