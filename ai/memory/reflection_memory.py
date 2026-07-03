import json
from pathlib import Path
from datetime import datetime


class ReflectionMemory:
    """
    Stores lessons learned from previous
    procurement decisions.
    """

    def __init__(self):

        self.file = Path("data/reflection_memory.json")

        self.file.parent.mkdir(

            parents=True,

            exist_ok=True

        )

        if not self.file.exists():

            self.save([])

    # ----------------------------------

    def load(self):

        try:

            with open(

                self.file,

                "r",

                encoding="utf-8"

            ) as f:

                content = f.read().strip()

                if not content:

                    self.save([])

                    return []

                return json.loads(content)

        except Exception:

            self.save([])

            return []

    # ----------------------------------

    def save(self, data):

        with open(

            self.file,

            "w",

            encoding="utf-8"

        ) as f:

            json.dump(

                data,

                f,

                indent=4

            )

    # ----------------------------------

    def add(

        self,

        decision,

        outcome,

        lesson

    ):

        data = self.load()

        data.append(

            {

                "timestamp": datetime.now().isoformat(),

                "decision": decision,

                "outcome": outcome,

                "lesson": lesson

            }

        )

        self.save(data)

    # ----------------------------------

    def get_recent(

        self,

        limit=20

    ):

        return self.load()[-limit:]


reflection_memory = ReflectionMemory()