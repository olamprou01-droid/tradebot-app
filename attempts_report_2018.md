# attempts_report.md — back-to-back Kraken Funded attempts, 2018-01-28 → 2026-10-12
Each attempt: start $10,000, PASS at +12%, KILL at −3% (static). Costs 4 bps/side + 2 bps slip + swap 0.0055%/4h. Data: ETHUSD, SOLUSD, LINKUSD, XRPUSD 4H/1D from Binance history (Kraken live).

| variant | attempts | PASS | KILL | pass rate | median days to pass | median days to kill | trades/attempt | attempt still open |
|---|---|---|---|---|---|---|---|---|
| base 0.25% | 2 | 2 | 0 | 100% | 1058 | nan | 104 | +6.7% after 143 trades |
| base 0.50% | 9 | 6 | 3 | 67% | 403 | 88 | 37 | -1.2% after 7 trades |
| ratchet 0.25→0.5→0.75% | 4 | 3 | 1 | 75% | 248 | 885 | 51 | +1.5% after 143 trades |
| breadth ≥2 coins up, 0.25% | 2 | 2 | 0 | 100% | 1058 | nan | 86 | +7.1% after 130 trades |
| breadth ≥2, ratchet | 3 | 3 | 0 | 100% | 750 | nan | 57 | +1.5% after 130 trades |
| breadth ≥3, ratchet | 3 | 2 | 1 | 67% | 649 | 1425 | 63 | +1.2% after 46 trades |
| don=30, 0.25% | 2 | 2 | 0 | 100% | 1058 | nan | 94 | +6.8% after 118 trades |
| vol≥1.5×, 0.25% | 2 | 2 | 0 | 100% | 1058 | nan | 93 | +9.4% after 126 trades |
| stop 1.5×ATR, 0.25% | 4 | 4 | 0 | 100% | 887 | nan | 98 | -2.6% after 14 trades |
| trail 4×ATR, 0.25% | 4 | 3 | 1 | 75% | 401 | 421 | 46 | +7.8% after 119 trades |
| don=30 + vol≥1.5 + ratchet, breadth≥2 | 2 | 2 | 0 | 100% | 1058 | nan | 70 | +6.9% after 97 trades |

## Edge by year (base rules, all signals, after costs)
| year | trades | win | net R/trade | avg breakout volume |
|---|---|---|---|---|
| 2018 | 6 | 33% | +0.26R | 1.8× |
| 2019 | 31 | 26% | +0.30R | 2.4× |
| 2020 | 73 | 40% | +1.07R | 2.0× |
| 2021 | 68 | 35% | +0.25R | 1.8× |
| 2022 | 22 | 32% | -0.35R | 2.0× |
| 2023 | 74 | 31% | +0.59R | 2.1× |
| 2024 | 70 | 29% | +0.18R | 2.1× |
| 2025 | 52 | 35% | +0.46R | 2.0× |
| 2026 | 44 | 23% | +0.32R | 2.3× |

## Attempt log — base 0.25%
| period | result | length | trades | win rate | avg breadth (coins in uptrend) | BTC move over attempt | avg breakout volume |
|---|---|---|---|---|---|---|---|
| 2018-01-28 → 2020-08-02 | **PASS** | 917 d | 69 | 38% | 2.2/4 | -0% | 2.2× |
| 2020-08-02 → 2023-11-13 | **PASS** | 1199 d | 139 | 35% | 2.9/4 | +217% | 2.0× |
| 2023-11-13 → 2026-10-12 | **OPEN** | 1064 d | 143 | 28% | 3.1/4 | +134% | 2.1× |

## Attempt log — base 0.50%
| period | result | length | trades | win rate | avg breadth (coins in uptrend) | BTC move over attempt | avg breakout volume |
|---|---|---|---|---|---|---|---|
| 2018-01-28 → 2020-02-16 | **PASS** | 749 d | 40 | 32% | 2.0/4 | -12% | 2.3× |
| 2020-02-16 → 2020-08-02 | **PASS** | 168 d | 28 | 43% | 2.4/4 | +13% | 2.1× |
| 2020-08-02 → 2021-02-06 | **PASS** | 188 d | 30 | 33% | 2.6/4 | +249% | 1.9× |
| 2021-02-06 → 2023-11-13 | **PASS** | 1011 d | 106 | 36% | 3.0/4 | -9% | 2.0× |
| 2023-11-13 → 2025-07-23 | **PASS** | 618 d | 88 | 32% | 3.1/4 | +233% | 2.0× |
| 2025-07-23 → 2026-04-27 | **KILL** | 278 d | 23 | 26% | 3.2/4 | -35% | 2.1× |
| 2026-04-27 → 2026-07-24 | **KILL** | 88 d | 10 | 10% | 2.8/4 | -16% | 2.3× |
| 2026-07-24 → 2026-08-30 | **PASS** | 38 d | 6 | 33% | 2.6/4 | +23% | 2.3× |
| 2026-08-30 → 2026-09-15 | **KILL** | 15 d | 6 | 0% | 4.0/4 | -3% | 2.9× |
| 2026-09-15 → 2026-10-12 | **OPEN** | 28 d | 7 | 43% | 4.0/4 | +9% | 2.1× |

## Attempt log — ratchet 0.25→0.5→0.75%
| period | result | length | trades | win rate | avg breadth (coins in uptrend) | BTC move over attempt | avg breakout volume |
|---|---|---|---|---|---|---|---|
| 2018-01-28 → 2020-06-02 | **PASS** | 856 d | 54 | 35% | 2.1/4 | -14% | 2.2× |
| 2020-06-02 → 2021-02-06 | **PASS** | 248 d | 41 | 34% | 2.5/4 | +305% | 2.0× |
| 2021-02-06 → 2023-07-11 | **KILL** | 885 d | 96 | 31% | 3.0/4 | -22% | 2.0× |
| 2023-07-11 → 2023-11-13 | **PASS** | 126 d | 12 | 58% | 3.2/4 | +17% | 2.1× |
| 2023-11-13 → 2026-10-12 | **OPEN** | 1064 d | 143 | 28% | 3.1/4 | +134% | 2.1× |

## Attempt log — breadth ≥2, ratchet
| period | result | length | trades | win rate | avg breadth (coins in uptrend) | BTC move over attempt | avg breakout volume |
|---|---|---|---|---|---|---|---|
| 2018-01-28 → 2020-02-17 | **PASS** | 750 d | 28 | 39% | 2.5/4 | -10% | 2.2× |
| 2020-02-17 → 2021-05-10 | **PASS** | 448 d | 69 | 35% | 3.0/4 | +458% | 1.9× |
| 2021-05-10 → 2023-11-12 | **PASS** | 915 d | 73 | 38% | 3.2/4 | -35% | 2.1× |
| 2023-11-12 → 2026-10-12 | **OPEN** | 1066 d | 130 | 28% | 3.4/4 | +124% | 2.1× |
