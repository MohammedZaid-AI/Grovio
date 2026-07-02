from difflib import SequenceMatcher


class ProductMatcher:

    def __init__(self):

        self.aliases = {

            "coke": ["coca-cola", "coca cola"],

            "pepsi": ["pepsi"],

            "sprite": ["sprite"],

            "thumbs up": ["thumbs up", "thums up"],

            "milk": ["milk"],

            "bread": ["bread"],

            "butter": ["butter"],

            "ice cream": ["ice cream"],

            "curd": ["curd", "dahi"],

            "paneer": ["paneer"]

        }

    def similarity(self, a, b):

        return SequenceMatcher(

            None,

            a.lower(),

            b.lower()

        ).ratio()

    def should_auto_select(

        self,

        query,

        products

    ):

        if not products:

            return False, None

        q = query.lower().strip()

        # ----------------------------------
        # Alias / Brand Matching
        # ----------------------------------

        if q in self.aliases:

            for alias in self.aliases[q]:

                for product in products:

                    if alias in product["displayName"].lower():

                        return True, product

        # ----------------------------------
        # Exact Substring Match
        # ----------------------------------

        for product in products:

            if q in product["displayName"].lower():

                return True, product

        # ----------------------------------
        # Fuzzy Matching
        # ----------------------------------

        best_score = 0

        best_product = None

        for product in products:

            score = self.similarity(

                q,

                product["displayName"]

            )

            if score > best_score:

                best_score = score

                best_product = product

        if best_score >= 0.90:

            return True, best_product

        return False, None


matcher = ProductMatcher()