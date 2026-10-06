# list_images.py
import oci

COMPARTMENT_ID = "ocid1.tenancy.oc1..aaaaaaaa23aiavctaqtlk4k4nkguu6sbrrjqhttfrncq2blbjf65fm4ax7hq"  # celui que tu as déjà récupéré

config = oci.config.from_file()  # lit ~/.oci/config
compute_client = oci.core.ComputeClient(config)

images = compute_client.list_images(
    compartment_id=COMPARTMENT_ID,
    operating_system="Canonical Ubuntu",
    shape="VM.Standard.A1.Flex",   # filtre : compatible avec le shape ARM
    sort_by="TIMECREATED",
    sort_order="DESC",
).data

if not images:
    print("Aucune image trouvée — vérifie le COMPARTMENT_ID.")
else:
    for img in images:
        print(f"{img.display_name:45s} → {img.id}")