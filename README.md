# Real Exchange Rate Explorer

A Streamlit app that computes the bilateral **real exchange rate (RER)** between any two IMF economies, including the Euro area. It uses monthly IMF CPI and exchange-rate data starting in January 1976, where the data exist.

## Features
- Pick **Country 1** and **Country 2** from the full ISO country list or the **Euro area (G163)**, with a one-click swap button.
- **Sample-period slider**: RER* is recalculated whenever you move it.
- **Real / Nominal / Both** chart toggle. In *Both* mode each series gets its own y-axis. An optional log scale is available for nominal S(t).
- KPI cards show RER*, the latest real index, and the latest nominal rate.
- **What does this mean?** section with three tabs:
  - *Plain English*: a shopping-basket explanation of today's real index.
  - *What drove it*: splits the RER change into the exchange-rate move and each country's inflation.
  - *Play with it*: sliders for a currency move and inflation, plus the purchasing power parity (PPP) benchmark.
- **CSV download** of every observation and calculation in the selected sample.
- A coverage panel shows where each series starts and ends, and how many months are missing.

## Economics
| Symbol | Definition |
|---|---|
| e_i(t) | IMF rate: units of country *i* currency per USD (period average) |
| **S(t)** | Units of Country 2 currency per 1 unit of Country 1 currency = e₂(t) / e₁(t) |
| P1(t), P2(t) | Monthly CPI (index) for Country 1 and Country 2 |
| **RER(t)** | S(t) × P1(t) / P2(t) |
| **RER\*** | Mean of RER(t) over the *valid* months in the selected sample |
| **Real Index(t)** | 100 × RER(t) / RER\* |

- Nominal S(t) is plotted in actual currency units and is **never normalized**.
- A rising Real Index means a **real appreciation of Country 1's currency**: Country 1's goods are getting more expensive relative to Country 2's.
- A month counts as valid only if all four inputs exist and are positive. Gaps are left blank: they are not interpolated, and the chart lines break across them.
- The USA's exchange rate is 1 by definition, so the app does not fetch it.

## Data source
IMF SDMX 3.0 API at `https://api.imf.org/external/sdmx/3.0`, agency `IMF.STA`:

| Dataflow | Key | Meaning |
|---|---|---|
| `CPI` | `{ISO3}.CPI._T.IX.M` | All-items CPI, index, monthly |
| `ER` | `{ISO3}.XDC_USD.PA_RT.M` | National currency per USD, period average, monthly |

Request pattern: `/data/dataflow/IMF.STA/{FLOW}/+/{KEY}`. Here `+` means the latest stable dataflow version, with `~` (latest) as the fallback.

Robustness:
- The app requests SDMX-CSV first and falls back to SDMX-JSON.
- If `XDC_USD` returns nothing, the app tries `USD_XDC` and inverts it. Some IMF releases code the rate in that direction.
- Results are cached for 24 hours. **Refresh IMF data** in the sidebar clears the cache.
- If your IMF API tier requires a key, set it before running:
  ```bash
  export IMF_SUBSCRIPTION_KEY=your_key   # sent as Ocp-Apim-Subscription-Key
  ```

### Coverage caveats
- Coverage varies widely. Many countries start well after 1976, and some have breaks. The app tells you which series are missing.
- **Euro area (G163)**: euro exchange rates begin in 1999. Euro-area CPI (HICP) usually starts in the 1990s.
- **Euro member countries**: the IMF may report pre-1999 rates either in the legacy national currency or converted to euro equivalents. Check the coverage panel and the CSV.
- Countries that redenominated (for example Turkey in 2005 or Venezuela) can show level breaks in S(t). The log-scale toggle helps when reading these series.

## Setup (Python 3.12)
```bash
python3.12 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
streamlit run app.py
```
The app opens at http://localhost:8501.

## CSV columns
`month, country1, country2, {ISO1}_LCU_per_USD, {ISO2}_LCU_per_USD, P1_CPI_{ISO1}, P2_CPI_{ISO2}, S_{ISO2}_per_{ISO1}, RER, valid_month, RER_star_sample_mean, Real_Index`
