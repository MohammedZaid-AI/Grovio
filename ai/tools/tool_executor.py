class ToolExecutor:

    def __init__(

        self,

        registry

    ):

        self.registry = registry

    async def execute(

        self,

        tool_name,

        **kwargs

    ):

        tool = self.registry.get(

            tool_name

        )

        if tool is None:

            raise Exception(

                f"Unknown tool {tool_name}"

            )

        return await tool.execute(

            **kwargs

        )