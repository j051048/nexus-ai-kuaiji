"""Chat uses the company budget and immutable model_costs, never a separate quota."""


class AssistantMeter:
    def __init__(self, db, shared_meter):
        self.db = db
        self.shared = shared_meter

    def context(self, actor, turn):
        run = dict(turn)
        run.update(kind="chat", event_id=None)
        return self.shared.context(actor, run)
