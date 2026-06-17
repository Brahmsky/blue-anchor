import glob
import pandas as pd
csv_files = sorted(glob.glob('datasources/local_ship_docs/outputs/benchmark/*judged*.csv'))

for f in csv_files[-1:]:
    df = pd.read_csv(f)
    fails = df[(df['text_only_Score'] == 1) & (df['graph_text_hybrid_Score'] != 1)]
    print(f"File: {f}")
    for idx, row in fails.iterrows():
        print(f"Q: {row['Question']}")
        print(f"Hybrid Answer ({row['graph_text_hybrid_Score']}): {row['graph_text_hybrid_Answer'][:100]}")
        print(f"Text Answer ({row['text_only_Score']}): {row['text_only_Answer'][:100]}")
        print("-" * 40)
