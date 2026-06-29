from ai.agents.dashboard_agent import DashboardAgent
from ai.agents.procurement_agent import ProcurementAgent
from ai.agents.ai_coo import AICOO


class DailyScheduler:

    """
    Generates the daily restaurant briefing.
    """

    def __init__(self):

        self.dashboard = DashboardAgent()

        self.procurement = ProcurementAgent()

        self.coo = AICOO()

    def execute(self):

        dashboard = self.dashboard.execute()

        purchase_order = self.procurement.execute()

        coo = self.coo.analyze()

        return {

            "dashboard": dashboard,

            "purchase_order": purchase_order,

            "coo": coo

        }


if __name__ == "__main__":

    from pprint import pprint

    scheduler = DailyScheduler()

    pprint(

        scheduler.execute()

    )