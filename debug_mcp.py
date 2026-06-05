from mcp_use import MCPClient

client = MCPClient.from_config_file("mcp.json")

print(type(client))
print(dir(client))