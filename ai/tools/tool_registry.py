from langchain_core.tools import tool

# Intelligence
from ai.intelligence.memory import RestaurantMemory
from ai.intelligence.supplier_memory import SupplierMemory
from ai.intelligence.pattern_detector import PatternDetector
from ai.intelligence.price_tracker import PriceTracker

# Procurement
from ai.agents.procurement_forecaster import ProcurementForecaster

# Reports
from ai.reports.daily_brief import generate_daily_brief

# Agents
from ai.agents.ai_coo import RestaurantCOO


# -----------------------------
# Memory Tool
# -----------------------------

@tool
def restaurant_memory():

    """
    Returns restaurant statistics and memory.
    """

    memory = RestaurantMemory()

    return {

        "completed_orders":

            memory.total_completed_orders(),

        "pending_orders":

            memory.total_pending_orders(),

        "restaurant_spend":

            memory.total_spend(),

        "average_order":

            memory.average_order_value()

    }


# -----------------------------
# Supplier Tool
# -----------------------------

@tool
def supplier_memory():

    """
    Returns supplier report.
    """

    memory = SupplierMemory()

    return memory.supplier_report()


# -----------------------------
# Pattern Tool
# -----------------------------

@tool
def buying_patterns():

    """
    Returns purchasing patterns.
    """

    detector = PatternDetector()

    return detector.detect_patterns()


# -----------------------------
# Forecast Tool
# -----------------------------

@tool
def procurement_forecast():

    """
    Returns procurement forecast.
    """

    forecast = ProcurementForecaster()

    return forecast.forecast()


# -----------------------------
# Daily Brief
# -----------------------------

@tool
def daily_brief():

    """
    Returns today's restaurant brief.
    """

    return generate_daily_brief()


# -----------------------------
# AI COO
# -----------------------------

@tool
def restaurant_coo():

    """
    Complete restaurant analysis.
    """

    coo = RestaurantCOO()

    return coo.analyze()


# -----------------------------
# Price Tracker
# -----------------------------

@tool
def product_price(product: str):

    """
    Returns price trend of a product.
    """

    tracker = PriceTracker()

    return tracker.analyze(product)


TOOLS = [

    restaurant_memory,

    supplier_memory,

    buying_patterns,

    procurement_forecast,

    daily_brief,

    restaurant_coo,

    product_price

]