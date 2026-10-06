import oci

config = oci.config.from_file()
identity_client = oci.identity.IdentityClient(config)

# Le tenancy OCID sert aussi de compartment_id racine pour lister les AD
tenancy_id = config["tenancy"]

ads = identity_client.list_availability_domains(compartment_id=tenancy_id).data

for ad in ads:
    print(ad.name)