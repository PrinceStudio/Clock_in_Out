from app.database import SessionLocal, engine, Base
from app.models import Employee, Site
from app.auth import hash_password


def seed_database():
    Base.metadata.create_all(bind=engine)

    db = SessionLocal()
    try:
        if db.query(Employee).count() > 0:
            return

        admin = Employee(
            name="Admin",
            password_hash=hash_password("admin123"),
            is_admin=True,
            active=True,
        )
        emp1 = Employee(
            name="John Doe",
            password_hash=hash_password("john2024"),
            is_admin=False,
            active=True,
        )
        emp2 = Employee(
            name="Jane Smith",
            password_hash=hash_password("jane2024"),
            is_admin=False,
            active=True,
        )
        emp3 = Employee(
            name="Bob Wilson",
            password_hash=hash_password("bob2024"),
            is_admin=False,
            active=True,
        )
        db.add_all([admin, emp1, emp2, emp3])

        sites = [
            Site(name="Site A - Main Office"),
            Site(name="Site B - Warehouse"),
            Site(name="Site C - Remote"),
        ]
        db.add_all(sites)

        db.commit()
    finally:
        db.close()
