class WhatsAppFormatter:

    """
    Formats all outgoing WhatsApp
    messages.
    """

    @staticmethod
    def welcome():

        return (
            "👋 *Welcome to Grovio AI COO*\n\n"
            "How can I help you today?\n\n"
            "1️⃣ Daily Brief\n"
            "2️⃣ Inventory\n"
            "3️⃣ Procurement Forecast\n"
            "4️⃣ AI COO Report\n"
            "5️⃣ Help"
        )

    @staticmethod
    def help():

        return (
            "📚 *Available Commands*\n\n"
            "• daily brief\n"
            "• inventory\n"
            "• forecast\n"
            "• report\n"
            "• upload invoice\n"
            "• help"
        )

    @staticmethod
    def unknown():

        return (
            "🤔 I didn't understand that.\n\n"
            "Type *help* to see available commands."
        )

    @staticmethod
    def success(message):

        return f"✅ {message}"

    @staticmethod
    def error(message):

        return f"❌ {message}"

    @staticmethod
    def short_report(

        completed,

        spend,

        low_stock

    ):

        text = (
            "📊 *Today's Restaurant Brief*\n\n"
            f"✅ Orders : {completed}\n"
            f"💰 Spend : ₹{spend}\n"
        )

        if low_stock:

            text += "\n⚠️ Low Stock\n"

            for item in low_stock:

                text += f"• {item['product']}\n"

        return text