import chromadb

client = chromadb.Client()
collection = client.create_collection("test")

collection.add(
    documents=["How does routing work?"],
    metadatas=[{"answer": "It uses a radix tree."}],
    ids=["id1"]
)

results = collection.query(
    query_texts=["Explain the router"],
    n_results=1
)
print("ChromaDB Results:", results)
