from pprint import pprint

from ai.langgraph.registry import registry

print("=" * 50)
print("COO")
print("=" * 50)

pprint(
    registry.execute("coo")
)

print()

print("=" * 50)
print("Decision")
print("=" * 50)

pprint(
    registry.execute("decision")
)