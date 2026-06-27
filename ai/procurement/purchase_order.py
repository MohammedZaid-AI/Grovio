from dataclasses import dataclass, field
from datetime import datetime
from typing import List


@dataclass
class PurchaseOrderItem:
    """
    Represents a single product
    inside a purchase order.
    """

    product: str
    quantity: float
    unit: str
    supplier: str = ""
    estimated_price: float = 0.0

    @property
    def subtotal(self) -> float:

        return round(

            self.quantity * self.estimated_price,

            2

        )


@dataclass
class PurchaseOrder:
    """
    Represents a complete purchase order.
    """

    supplier: str

    items: List[PurchaseOrderItem] = field(

        default_factory=list

    )

    status: str = "DRAFT"

    created_at: datetime = field(

        default_factory=datetime.now

    )

    notes: str = ""

    def add_item(

        self,

        item: PurchaseOrderItem

    ):

        self.items.append(item)

    @property
    def total_items(self):

        return len(self.items)

    @property
    def total_quantity(self):

        return round(

            sum(

                item.quantity

                for item in self.items

            ),

            2

        )

    @property
    def total_amount(self):

        return round(

            sum(

                item.subtotal

                for item in self.items

            ),

            2

        )

    def to_dict(self):

        return {

            "supplier": self.supplier,

            "status": self.status,

            "created_at": self.created_at.strftime(

                "%Y-%m-%d %H:%M:%S"

            ),

            "notes": self.notes,

            "total_items": self.total_items,

            "total_quantity": self.total_quantity,

            "total_amount": self.total_amount,

            "items": [

                {

                    "product": item.product,

                    "quantity": item.quantity,

                    "unit": item.unit,

                    "supplier": item.supplier,

                    "estimated_price": item.estimated_price,

                    "subtotal": item.subtotal

                }

                for item in self.items

            ]

        }


if __name__ == "__main__":

    po = PurchaseOrder(

        supplier="ABC Foods"

    )

    po.add_item(

        PurchaseOrderItem(

            product="Milk",

            quantity=20,

            unit="packets",

            supplier="ABC Foods",

            estimated_price=32

        )

    )

    po.add_item(

        PurchaseOrderItem(

            product="Bread",

            quantity=10,

            unit="loaves",

            supplier="ABC Foods",

            estimated_price=40

        )

    )

    from pprint import pprint

    pprint(

        po.to_dict()

    )