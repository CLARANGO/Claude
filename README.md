# TFU Prediction

Pipeline for predicting monthly TFU (Total Fee Usage) per user.

## Structure

```
.
├── sql/
│   └── build_tfu_user_monthly.sql   # Builds the tfu_user_monthly table
└── notebooks/
    ├── eda.ipynb                     # Exploratory data analysis
    └── score_monthly.ipynb           # Monthly scoring pipeline
```

## Components

- **`sql/build_tfu_user_monthly.sql`** — SQL that produces the
  `tfu_user_monthly` feature table used downstream.
- **`notebooks/eda.ipynb`** — Exploratory data analysis on the monthly
  user table.
- **`notebooks/score_monthly.ipynb`** — Loads the model and scores users
  for the current month.

## Getting started

1. Run the SQL in `sql/build_tfu_user_monthly.sql` against the warehouse
   to materialize `tfu_user_monthly`.
2. Open `notebooks/eda.ipynb` to inspect distributions and validate the
   table.
3. Run `notebooks/score_monthly.ipynb` to generate predictions.
