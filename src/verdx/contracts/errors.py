"""Domain exceptions for the verdx engine."""


class FourEyesError(Exception):
    pass


class InvalidTransitionError(FourEyesError):
    def __init__(self, from_state: str | None, event_type: str, to_state: str) -> None:
        self.from_state = from_state
        self.event_type = event_type
        self.to_state = to_state
        super().__init__(f"Invalid transition: {from_state} --[{event_type}]--> {to_state}")


class RoleNotPermittedError(FourEyesError):
    def __init__(self, role: str, from_state: str | None, to_state: str) -> None:
        self.role = role
        self.from_state = from_state
        self.to_state = to_state
        super().__init__(f"Role '{role}' not permitted for transition {from_state} --> {to_state}")


class GateArtifactMissingError(FourEyesError):
    def __init__(self, artifact_type: str, to_state: str) -> None:
        self.artifact_type = artifact_type
        self.to_state = to_state
        super().__init__(f"Gate artifact '{artifact_type}' required for transition to {to_state}")


class ReplayError(FourEyesError):
    pass


class StoreError(FourEyesError):
    pass
