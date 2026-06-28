class ConversationMemory:
    """
    Stores the current conversation state.

    This is temporary working memory.
    """

    def __init__(self):

        self.reset()

    # -----------------------------------

    def reset(self):

        self.purchase_order = None

        self.awaiting_confirmation = False

    # -----------------------------------

    def set_purchase_order(self, order):

        self.purchase_order = order

        self.awaiting_confirmation = True

    # -----------------------------------

    def get_purchase_order(self):

        return self.purchase_order

    # -----------------------------------

    def clear(self):

        self.reset()


memory = ConversationMemory()