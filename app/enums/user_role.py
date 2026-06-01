from enum import StrEnum


class UserRole(StrEnum):
    ADMIN = "admin"
    CLINICIAN = "clinician"
    VIEWER = "viewer"
