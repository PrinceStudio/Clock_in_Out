from sqlalchemy import Column, Integer, String, Boolean, DateTime, Date, ForeignKey
from app.database import Base


class Employee(Base):
    __tablename__ = "employees"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    password_hash = Column(String, nullable=False)
    is_admin = Column(Boolean, default=False)
    active = Column(Boolean, default=True)


class Shift(Base):
    __tablename__ = "shifts"

    id = Column(Integer, primary_key=True, index=True)
    employee_id = Column(Integer, ForeignKey("employees.id"), nullable=False)
    clock_in = Column(DateTime, nullable=False)
    clock_out = Column(DateTime, nullable=True)
    date = Column(Date, nullable=False)
    shift_type = Column(String, nullable=True) # "Day" or "Night"

