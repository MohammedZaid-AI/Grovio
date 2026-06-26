from typing import TypedDict, Optional, List, Dict, Any


class RestaurantState(TypedDict):

    # User message
    message: str

    # Intent selected by supervisor
    intent: Optional[str]

    # Final answer
    response: Optional[str]

    # Tool outputs
    memory: Optional[Dict[str, Any]]

    supplier: Optional[Dict[str, Any]]

    procurement: Optional[Dict[str, Any]]

    invoice: Optional[Dict[str, Any]]

    analytics: Optional[Dict[str, Any]]

    # Agent reasoning
    thoughts: List[str]