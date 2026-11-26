"""Create two sample AAS shells and operational Submodels in a BaSyx Go environment."""
import os
import base64
from aas_http import request, configuration
 
API_BASE = os.getenv("AAS_BASE_URL", "http://localhost:8082").rstrip("/")
BASE_URN = "urn:university:aas-demo"
SEMANTICS = {
    "MotorTemperature": "urn:university:aas-demo:semantic:MotorTemperature:Celsius",
    "MotorSpeed": "urn:university:aas-demo:semantic:MotorSpeed:RPM",
    "OperatingState": "urn:university:aas-demo:semantic:OperatingState:Boolean",
    "MotorCurrent": "urn:university:aas-demo:semantic:MotorCurrent:Ampere",
}
 
DEFINITIONS = {
    "MotorTemperature": "Simulated motor housing temperature in degrees Celsius.",
    "MotorSpeed": "Simulated shaft rotational speed in revolutions per minute (rpm).",
    "OperatingState": "True means simulated motor is running; false means stopped.",
    "MotorCurrent": "Simulated RMS current in ampere; illustrative signal only.",
}

def property_element(name, data_type, initial_value):
    return {
        "modelType": "Property", "idShort": name, "valueType": data_type,
        "description": [{"language": "en", "text": DEFINITIONS[name]}],
        "value": str(initial_value).lower() if isinstance(initial_value, bool) else str(initial_value),
        "semanticId": {"type": "ExternalReference", "keys": [
            {"type": "GlobalReference", "value": SEMANTICS[name]}
        ]},
    }
 
def submit(resource, data):
    response = request("POST", f"/{resource}", json=data, timeout=10)
    if response.status_code not in (200, 201, 409):
        raise RuntimeError(f"POST {resource}: HTTP {response.status_code} {response.text[:500]}")
    if response.status_code == 409:
        encoded = base64.urlsafe_b64encode(data["id"].encode()).decode().rstrip("=")
        existing = request("GET", f"/{resource}/{encoded}", timeout=10)
        existing.raise_for_status()
        actual = existing.json()
        keys = ("id", "idShort", "assetInformation", "submodels") if resource == "shells" else ("id", "idShort")
        if any(actual.get(key) != data.get(key) for key in keys):
            raise ValueError("Existing resource differs from requested model; inspect before reusing")
        if resource == "submodels":
            elements = {e["idShort"]: e for e in actual.get("submodelElements", [])}
            for wanted in data["submodelElements"]:
                found = elements.get(wanted["idShort"], {})
                if any(found.get(k) != wanted.get(k) for k in ("modelType", "valueType", "semanticId", "description")):
                    raise ValueError("Existing Property metadata differs; inspect before reusing")
    print(f"{resource}: HTTP {response.status_code} -> {data['idShort']}")
 
def main():
    configuration()
    for motor in ("A", "B"):
        asset_id = f"{BASE_URN}:asset:motor-{motor.lower()}"
        shell_id = f"{BASE_URN}:shell:motor-{motor.lower()}"
        submodel_id = f"{BASE_URN}:submodel:motor-{motor.lower()}:operational"
        submodel = {
            "modelType": "Submodel", "id": submodel_id,
            "idShort": "OperationalData",
            "submodelElements": [
                property_element("MotorTemperature", "xs:double", 0.0),
                property_element("MotorSpeed", "xs:double", 0.0),
                property_element("OperatingState", "xs:boolean", False),
                property_element("MotorCurrent", "xs:double", 0.0),
            ],
        }
        shell = {
            "modelType": "AssetAdministrationShell", "id": shell_id,
            "idShort": f"Motor{motor}AAS",
            "assetInformation": {"assetKind": "Instance", "globalAssetId": asset_id},
            "submodels": [{"type": "ModelReference", "keys": [
                {"type": "Submodel", "value": submodel_id}
            ]}],
        }
        submit("submodels", submodel)
        submit("shells", shell)
 
if __name__ == "__main__":
    main()
