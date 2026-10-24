from pathlib import Path


schema_path = Path(__file__).with_name("supabase_schema.sql")
print(f"Run the SQL in {schema_path} from your Supabase SQL Editor.")
print("This app no longer initializes or stores tickets in a local database.")
