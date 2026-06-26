from db import get_latest_price


class QuoteAnalyzer:

    def analyze(self, quote):

        report=[]

        for item in quote["items"]:

            history=get_latest_price(

                item["product"]

            )

            if history is None:

                report.append(

                    {

                        "product":item["product"],

                        "status":"NEW"

                    }

                )

                continue

            latest_price=history[1]

            diff=item["price"]-latest_price

            if diff<0:

                report.append(

                    {

                        "product":item["product"],

                        "status":"CHEAPER",

                        "difference":abs(diff)

                    }

                )

            elif diff>0:

                report.append(

                    {

                        "product":item["product"],

                        "status":"EXPENSIVE",

                        "difference":diff

                    }

                )

            else:

                report.append(

                    {

                        "product":item["product"],

                        "status":"SAME"

                    }

                )

        return report