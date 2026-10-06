"""
Script de provisioning ponctuel — PAS du code applicatif VintedPro.
Usage unique : créer l'instance VM.Standard.A1.Flex du VPS d'hébergement en
retentant automatiquement tant qu'Oracle renvoie "Out of capacity" (fréquent
sur les régions mono-AD comme eu-paris-1).

Prérequis :
    pip install oci
    ~/.oci/config configuré (voir OCI Console > My profile > API keys)
    Une paire de clés SSH locale (ssh-keygen -t rsa -b 4096 si besoin)
    Le VCN / subnet / security list déjà créés (persistants, indépendants
    de ce script) et l'OCID de l'image récupéré via list_images.py

Usage :
    python3 retry_instance.py
"""

import os
import sys
import time
from datetime import datetime

import oci

# --- À REMPLIR ---
COMPARTMENT_ID = "ocid1.tenancy.oc1..aaaaaaaa23aiavctaqtlk4k4nkguu6sbrrjqhttfrncq2blbjf65fm4ax7hq"  # racine du tenancy
SUBNET_ID = "ocid1.subnet.oc1.eu-paris-1.aaaaaaaaet26zpaqwi2vg3la7lu5wm73mdg2vrmtpzcwkyjpahnxg6fjl6aa"
IMAGE_ID = "ocid1.image.oc1.eu-paris-1.aaaaaaaaqb3hjmnty4rvidngmb2m4pqtmnrqingxqxqhutffjogbxw6hzcga"  # Ubuntu 24.04 aarch64, 2026.07.17
AVAILABILITY_DOMAIN = "Jvdo:EU-PARIS-1-AD-1"     # région mono-AD, un seul choix possible
SSH_PUBLIC_KEY_PATH = "~/.ssh/id_rsa.pub"        # clé SSH publique, PAS la clé API OCI

RETRY_INTERVAL_SECONDS = 90     # remonté après un 429 rencontré à 60s — compromis prudence/fréquence
MAX_RETRIES = 100000             # tourne quasi indéfiniment — arrête-le toi-même (Ctrl+C) quand besoin


def build_launch_details():
    ssh_key_path = os.path.expanduser(SSH_PUBLIC_KEY_PATH)
    with open(ssh_key_path, "r") as f:
        ssh_key = f.read()

    return oci.core.models.LaunchInstanceDetails(
        compartment_id=COMPARTMENT_ID,
        availability_domain=AVAILABILITY_DOMAIN,
        shape="VM.Standard.A1.Flex",
        shape_config=oci.core.models.LaunchInstanceShapeConfigDetails(
            ocpus=1.0, memory_in_gbs=6.0
        ),  # volontairement réduit vs 2/12 : plus facile à obtenir, à agrandir
            # ensuite via Console > Instance > Edit (redémarrage requis, pas de recréation)
            # ocpus/memory en float explicite -- Oracle peut être strict sur le type
        display_name="vintedpro-vps",
        create_vnic_details=oci.core.models.CreateVnicDetails(
            subnet_id=SUBNET_ID, assign_public_ip=True
        ),
        source_details=oci.core.models.InstanceSourceViaImageDetails(image_id=IMAGE_ID),
        metadata={"ssh_authorized_keys": ssh_key.strip()},  # .strip() -- retire un éventuel \r\n de fin de fichier (lecture Windows)
    )


def main():
    config = oci.config.from_file()  # lit ~/.oci/config
    compute_client = oci.core.ComputeClient(config)
    launch_details = build_launch_details()

    print(f"Démarrage des tentatives (intervalle {RETRY_INTERVAL_SECONDS}s, max {MAX_RETRIES})...")

    # Debug ponctuel : affiche le JSON exact qui sera envoyé, pour diagnostiquer
    # une éventuelle erreur "CannotParseRequest" avant de lancer les tentatives.
    import json
    print("--- Corps de la requête (debug) ---")
    print(json.dumps(oci.util.to_dict(launch_details), indent=2, ensure_ascii=False))
    print("------------------------------------")

    for attempt in range(1, MAX_RETRIES + 1):
        now = datetime.now().strftime("%H:%M:%S")
        try:
            response = compute_client.launch_instance(launch_details)
            print(f"\nSUCCÈS à la tentative {attempt} ({now}) !")
            print(f"Instance OCID : {response.data.id}")
            print("Va dans la console Oracle pour voir l'IP publique une fois l'instance 'Running'.")
            return

        except oci.exceptions.ServiceError as e:
            if "Out of capacity" in str(e.message) or e.status == 500:
                print(f"[{now}] Tentative {attempt}/{MAX_RETRIES} — toujours out of capacity, "
                      f"retry dans {RETRY_INTERVAL_SECONDS}s...")
                time.sleep(RETRY_INTERVAL_SECONDS)
            elif e.status == 429:
                backoff = max(RETRY_INTERVAL_SECONDS * 5, 300)  # au moins 5 min de pause
                print(f"[{now}] Tentative {attempt}/{MAX_RETRIES} — 429 Too Many Requests, "
                      f"pause plus longue de {backoff}s avant de retenter...")
                time.sleep(backoff)
            else:
                print(f"[{now}] Erreur inattendue (pas liée à la capacité) : {e.status} — {e.message}")
                print(f"  code           : {getattr(e, 'code', 'N/A')}")
                print(f"  target_service : {getattr(e, 'target_service', 'N/A')}")
                print(f"  operation_name : {getattr(e, 'operation_name', 'N/A')}")
                print(f"  request_id     : {getattr(e, 'request_id', 'N/A')}")
                print("Arrêt du script — vérifie la config (OCID, clé SSH, etc.) avant de relancer.")
                sys.exit(1)

    print(f"Abandon après {MAX_RETRIES} tentatives sans succès.")


if __name__ == "__main__":
    main()