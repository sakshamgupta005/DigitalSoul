from .errors import GhostError


async def verify(driver, step, phase):
    for condition in step[phase]:
        if not await driver.check(step["app"], condition, step["locators"]):
            raise GhostError(f"{phase.upper()}CONDITION_FAILED", f"{phase}condition did not match")
