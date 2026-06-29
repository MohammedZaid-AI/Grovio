from db import *

from pprint import pprint

pprint(get_all_purchase_invoices())

print()

if get_all_purchase_invoices():

    invoice = get_all_purchase_invoices()[0]

    pprint(

        get_invoice_items(

            invoice[0]

        )

    )