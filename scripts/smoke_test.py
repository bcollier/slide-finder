"""Step 3: 15 hand-typed slide cards in a persistent collection.

Run it twice. The first run adds the cards; the second run should find them already there.
Card text is paraphrased from real slides in 45-851 Data Mining, Spring 2025 (M4).
"""
import sys

import chromadb

T = "2025 Spring (M4)"
CARDS = [
    # (slide id, session, slide number, card text)
    ("dm-s05-003", "Clustering with K-Means", 3, "Customer Segmentation: sorting customers into meaningful groups."),
    ("dm-s05-007", "Clustering with K-Means", 7, "K-Means clustering basic steps: choose k centroids, assign each point to the nearest centroid, recompute centroids, repeat."),
    ("dm-s05-019", "Clustering with K-Means", 19, "Elbow method: plot SSE for each value of K and pick the K where the drop in SSE levels off."),
    ("dm-s05-020", "Clustering with K-Means", 20, "Silhouette method: cohesion within a cluster versus separation from the nearest other cluster."),
    ("dm-s05-028", "Clustering with K-Means", 28, "Hierarchical clustering produces a dendrogram, a tree structure of nested clusters."),
    ("dm-s05-037", "Clustering with K-Means", 37, "DBSCAN: density-based spatial clustering of applications with noise; groups points in dense regions and marks outliers."),
    ("dm-s10-013", "Overview of regression", 13, "Assumptions of linear regression: linearity, independence, constant variance, normal residuals."),
    ("dm-s10-024", "Overview of regression", 24, "One-hot encoding and dummy encoding for categorical variables."),
    ("dm-s10-031", "Overview of regression", 31, "Scaling by standardization: center features at 0 with unit standard deviation."),
    ("dm-s15-017", "Overview of classification", 17, "Generalization and overfitting: the bias-variance tradeoff."),
    ("dm-s15-023", "Overview of classification", 23, "K-fold cross validation for model validation on smaller datasets."),
    ("dm-s15-030", "Overview of classification", 30, "Building a confusion matrix: true positives, false positives, true negatives, false negatives."),
    ("dm-s15-061", "Overview of classification", 61, "Random forests: build many trees on random samples of rows, then take a majority vote."),
    ("dm-s22-008", "Forecasting with Time Series Data", 8, "ARIMA models: autoregressive and moving average processes for time series."),
    ("dm-s23-033", "Natural language processing", 33, "TF-IDF: words that are rarer across documents get more weight."),
]

client = chromadb.PersistentClient(path="chroma_store")
col = client.get_or_create_collection(name="smoke")
print("items before:", col.count())

if col.count() == 0:
    col.add(
        ids=[c[0] for c in CARDS],
        documents=[c[3] for c in CARDS],
        metadatas=[{"course": "45-851", "term": T, "session": c[1], "slide_number": c[2]} for c in CARDS],
    )
    print("added", len(CARDS), "cards; items now:", col.count())

query = " ".join(sys.argv[1:]) or "how many groups should I split my customers into"
res = col.query(query_texts=[query], n_results=3)
print(f"\nquery: {query!r}")
for id_, dist, doc in zip(res["ids"][0], res["distances"][0], res["documents"][0]):
    print(f"  {dist:.3f}  {id_}  {doc[:70]}")
