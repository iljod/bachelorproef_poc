from pydantic import BaseModel


class SessionRequest(BaseModel):
    students: list[str]
    ttl_type: str = "class"


class StopRequest(BaseModel):
    students: list[str]


class RedeployRequest(BaseModel):
    students: list[str]
    ttl_type: str = "class"


class AddClassRequest(BaseModel):
    class_name: str


class AddStudentRequest(BaseModel):
    student_id: str
