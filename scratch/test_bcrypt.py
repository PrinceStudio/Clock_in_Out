import bcrypt

# Let's test the native bcrypt package with the exact hash from the database
db_hash = "$2b$12$mASXkd.nEFWP7g/MZgQZKelTsiAIllxXwdD6nWzYqzby6Ds6QjaLm"
print("Verify database hash:", bcrypt.checkpw(b"admin123", db_hash.encode("utf-8")))

new_hash = bcrypt.hashpw(b"admin123", bcrypt.gensalt())
print("New hash:", new_hash)
print("Verify new hash:", bcrypt.checkpw(b"admin123", new_hash))
