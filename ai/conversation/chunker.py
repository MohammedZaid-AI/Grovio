class MessageChunker:
    """
    Splits long AI responses into
    WhatsApp-friendly chunks.
    """

    def __init__(

        self,

        chunk_size=1200

    ):

        self.chunk_size = chunk_size

    def split(

        self,

        text

    ):

        if len(text) <= self.chunk_size:

            return [text]

        chunks = []

        start = 0

        while start < len(text):

            end = start + self.chunk_size

            chunks.append(

                text[start:end]

            )

            start = end

        return chunks


chunker = MessageChunker()


if __name__ == "__main__":

    text = "A" * 3500

    parts = chunker.split(text)

    print(len(parts))