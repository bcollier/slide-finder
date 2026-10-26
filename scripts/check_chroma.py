"""Step 2: confirm Chroma imports and can create a collection."""
import chromadb

client = chromadb.PersistentClient(path="chroma_store")
collection = client.get_or_create_collection(name="hello")
print("chromadb", chromadb.__version__, "| collection:", collection.name, "| items:", collection.count())
