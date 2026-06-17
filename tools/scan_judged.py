import glob
import pandas as pd

csv_files = sorted(glob.glob('datasources/local_ship_docs/outputs/benchmark/*judged*.csv'))

for f in csv_files[-3:]:
    print(f"\n--- Analyzing {f} ---")
    df = pd.read_csv(f)
    print(f"Total queries: {len(df)}")
    cols = [c for c in df.columns if c.endswith('_Score') or c.endswith('_score')]
    for col in cols:
        print(f"{col}: values counts: {dict(df[col].value_counts())}")
