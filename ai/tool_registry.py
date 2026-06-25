from ai.tools import (
    daily_brief,
    forecast,
    history,
    pending,
    patterns,
)

TOOLS = {

    "daily_brief": daily_brief,

    "forecast": forecast,

    "history": history,

    "pending": pending,

    "patterns": patterns,

}


TOOL_DESCRIPTIONS = """

Available Tools

daily_brief
Returns today's restaurant summary.

forecast
Forecasts future grocery purchases.

history
Shows completed grocery orders.

pending
Shows pending approval orders.

patterns
Analyzes restaurant buying patterns.

"""