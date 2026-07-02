from ai.tools.search_products_tool import SearchProductsTool


class ToolRegistry:
    """
    Central registry for all executable tools.
    """

    def __init__(self):

        self.tools = {}

        self.register(
            SearchProductsTool()
        )

    # ----------------------------------
    # Register Tool
    # ----------------------------------

    def register(

        self,

        tool

    ):

        self.tools[tool.name] = tool

    # ----------------------------------
    # Get Tool
    # ----------------------------------

    def get(

        self,

        name

    ):

        return self.tools.get(name)

    # ----------------------------------
    # Execute Tool
    # ----------------------------------

    async def execute(

        self,

        name,

        **kwargs

    ):

        tool = self.get(name)

        if tool is None:

            raise ValueError(

                f"Unknown tool: {name}"

            )

        return await tool.execute(

            **kwargs

        )

    # ----------------------------------
    # Available Tools
    # ----------------------------------

    def available(self):

        return list(

            self.tools.keys()

        )


tool_registry = ToolRegistry()