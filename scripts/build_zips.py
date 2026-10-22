"""Package the two Lambda handlers (stdlib + boto3 from the runtime)."""
import os
import zipfile

os.makedirs("build", exist_ok=True)
for name, dirs in [("queue-consumer", ["common", "queue_consumer"]), ("sync-processor", ["common", "sync_api"])]:
    with zipfile.ZipFile(f"build/{name}.zip", "w", zipfile.ZIP_DEFLATED) as z:
        for d in dirs:
            for root, _, files in os.walk(os.path.join("src", d)):
                for f in files:
                    if not f.endswith(".pyc"):
                        p = os.path.join(root, f)
                        z.write(p, os.path.relpath(p, "src"))
    print(name, os.path.getsize(f"build/{name}.zip"), "bytes")
