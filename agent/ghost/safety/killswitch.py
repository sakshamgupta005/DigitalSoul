class KillSwitch:
    """Latched stop. Reset is an explicit local operator action."""
    def __init__(self):
        self.engaged = False
        self._tasks = set()

    def track(self, task):
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def engage(self):
        self.engaged = True
        for task in list(self._tasks):
            task.cancel()

    def reset(self):
        if self._tasks:
            raise RuntimeError("Wait for active driver tasks to stop before reset")
        self.engaged = False
