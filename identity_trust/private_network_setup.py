import argparse
import json
import os
from pathlib import Path
from typing import Any

import boto3
from botocore.exceptions import ClientError


BASE_DIR = Path(__file__).parent
SUPPORTED_AZ_IDS = {"euw2-az1", "euw2-az2", "euw2-az3"}
ENDPOINT_SERVICES = [
    "bedrock-agentcore",
    "bedrock-agentcore.gateway",
    "bedrock-runtime",
    "ecr.api",
    "ecr.dkr",
    "logs",
]


def env_list(name: str) -> list[str]:
    return [item.strip() for item in os.getenv(name, "").split(",") if item.strip()]


def default_vpc(ec2) -> str:
    response = ec2.describe_vpcs(Filters=[{"Name": "is-default", "Values": ["true"]}])
    vpcs = response["Vpcs"]
    if not vpcs:
        raise RuntimeError("No default VPC found. Pass --vpc-id and --subnet-ids.")
    return vpcs[0]["VpcId"]


def default_subnets(ec2, vpc_id: str) -> list[str]:
    response = ec2.describe_subnets(
        Filters=[
            {"Name": "vpc-id", "Values": [vpc_id]},
            {"Name": "state", "Values": ["available"]},
        ]
    )
    subnets = [
        subnet
        for subnet in response["Subnets"]
        if subnet.get("AvailabilityZoneId") in SUPPORTED_AZ_IDS
    ]
    subnets.sort(key=lambda item: item["AvailabilityZoneId"])
    if len(subnets) < 2:
        raise RuntimeError(
            "At least two subnets in supported AZs are recommended. "
            "Pass --subnet-ids explicitly."
        )
    return [subnet["SubnetId"] for subnet in subnets[:2]]


def ensure_security_group(ec2, vpc_id: str, name: str) -> str:
    response = ec2.describe_security_groups(
        Filters=[
            {"Name": "vpc-id", "Values": [vpc_id]},
            {"Name": "group-name", "Values": [name]},
        ]
    )
    if response["SecurityGroups"]:
        security_group_id = response["SecurityGroups"][0]["GroupId"]
        print(f"Using existing private scenario security group: {security_group_id}")
    else:
        security_group_id = ec2.create_security_group(
            GroupName=name,
            Description="Prompt 4 private Lambda/Runtime/endpoint security group.",
            VpcId=vpc_id,
            TagSpecifications=[
                {
                    "ResourceType": "security-group",
                    "Tags": [{"Key": "ArchitecturePattern", "Value": "Prompt4IdentityTrust"}],
                }
            ],
        )["GroupId"]
        print(f"Created private scenario security group: {security_group_id}")

    try:
        ec2.authorize_security_group_ingress(
            GroupId=security_group_id,
            IpPermissions=[
                {
                    "IpProtocol": "tcp",
                    "FromPort": 443,
                    "ToPort": 443,
                    "UserIdGroupPairs": [{"GroupId": security_group_id}],
                }
            ],
        )
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") != "InvalidPermission.Duplicate":
            raise

    return security_group_id


def ensure_endpoint(ec2, vpc_id: str, subnet_ids: list[str], security_group_id: str, region: str, short_name: str) -> str:
    service_name = f"com.amazonaws.{region}.{short_name}"
    existing = ec2.describe_vpc_endpoints(
        Filters=[
            {"Name": "vpc-id", "Values": [vpc_id]},
            {"Name": "service-name", "Values": [service_name]},
        ]
    )["VpcEndpoints"]
    active = [
        endpoint
        for endpoint in existing
        if endpoint["State"] not in {"deleted", "deleting", "failed", "rejected"}
    ]
    if active:
        endpoint_id = active[0]["VpcEndpointId"]
        print(f"Using existing VPC endpoint {service_name}: {endpoint_id}")
        return endpoint_id

    endpoint_id = ec2.create_vpc_endpoint(
        VpcId=vpc_id,
        ServiceName=service_name,
        VpcEndpointType="Interface",
        SubnetIds=subnet_ids,
        SecurityGroupIds=[security_group_id],
        PrivateDnsEnabled=True,
        TagSpecifications=[
            {
                "ResourceType": "vpc-endpoint",
                "Tags": [{"Key": "ArchitecturePattern", "Value": "Prompt4IdentityTrust"}],
            }
        ],
    )["VpcEndpoint"]["VpcEndpointId"]
    print(f"Created VPC endpoint {service_name}: {endpoint_id}")
    return endpoint_id


def route_table_ids_for_subnets(ec2, vpc_id: str, subnet_ids: list[str]) -> list[str]:
    route_tables = ec2.describe_route_tables(
        Filters=[{"Name": "vpc-id", "Values": [vpc_id]}]
    )["RouteTables"]
    selected = set()
    main = None
    for route_table in route_tables:
        for association in route_table.get("Associations", []):
            if association.get("Main"):
                main = route_table["RouteTableId"]
            if association.get("SubnetId") in subnet_ids:
                selected.add(route_table["RouteTableId"])
    if not selected and main:
        selected.add(main)
    if not selected:
        raise RuntimeError("Could not resolve route tables for private scenario subnets")
    return sorted(selected)


def ensure_s3_gateway_endpoint(ec2, vpc_id: str, subnet_ids: list[str], region: str) -> str:
    service_name = f"com.amazonaws.{region}.s3"
    existing = ec2.describe_vpc_endpoints(
        Filters=[
            {"Name": "vpc-id", "Values": [vpc_id]},
            {"Name": "service-name", "Values": [service_name]},
        ]
    )["VpcEndpoints"]
    active = [
        endpoint
        for endpoint in existing
        if endpoint["State"] not in {"deleted", "deleting", "failed", "rejected"}
    ]
    if active:
        endpoint_id = active[0]["VpcEndpointId"]
        print(f"Using existing VPC gateway endpoint {service_name}: {endpoint_id}")
        return endpoint_id

    endpoint_id = ec2.create_vpc_endpoint(
        VpcId=vpc_id,
        ServiceName=service_name,
        VpcEndpointType="Gateway",
        RouteTableIds=route_table_ids_for_subnets(ec2, vpc_id, subnet_ids),
        TagSpecifications=[
            {
                "ResourceType": "vpc-endpoint",
                "Tags": [{"Key": "ArchitecturePattern", "Value": "Prompt4IdentityTrust"}],
            }
        ],
    )["VpcEndpoint"]["VpcEndpointId"]
    print(f"Created VPC gateway endpoint {service_name}: {endpoint_id}")
    return endpoint_id


def write_env_file(path: Path, values: dict[str, str]) -> None:
    lines = [f"{key}={value}" for key, value in values.items()]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create VPC inputs for private Lambda -> private AgentCore Runtime tests."
    )
    parser.add_argument("--profile", help="AWS profile to use for setup.")
    parser.add_argument("--region", default=os.getenv("AWS_REGION", "eu-west-2"))
    parser.add_argument("--vpc-id", default=os.getenv("PROMPT4_PRIVATE_VPC_ID"))
    parser.add_argument("--subnet-ids", nargs="*", default=env_list("PROMPT4_PRIVATE_SUBNET_IDS"))
    parser.add_argument(
        "--security-group-name",
        default=os.getenv("PROMPT4_PRIVATE_SECURITY_GROUP_NAME", "prompt4-private-agentcore"),
    )
    parser.add_argument(
        "--env-file",
        default=str(BASE_DIR / "private_network.env"),
    )
    args = parser.parse_args()

    session = boto3.Session(profile_name=args.profile) if args.profile else boto3.Session()
    ec2 = session.client("ec2", region_name=args.region)
    vpc_id = args.vpc_id or default_vpc(ec2)
    subnet_ids = args.subnet_ids or default_subnets(ec2, vpc_id)
    security_group_id = ensure_security_group(ec2, vpc_id, args.security_group_name)
    endpoint_ids = {
        short_name: ensure_endpoint(ec2, vpc_id, subnet_ids, security_group_id, args.region, short_name)
        for short_name in ENDPOINT_SERVICES
    }
    s3_endpoint_id = ensure_s3_gateway_endpoint(ec2, vpc_id, subnet_ids, args.region)

    values = {
        "PROMPT4_PRIVATE_VPC_ID": vpc_id,
        "PROMPT4_PRIVATE_SUBNET_IDS": ",".join(subnet_ids),
        "PROMPT4_PRIVATE_SECURITY_GROUP_IDS": security_group_id,
        "PROMPT4_PRIVATE_AGENTCORE_ENDPOINT_ID": endpoint_ids["bedrock-agentcore"],
        "PROMPT4_PRIVATE_GATEWAY_ENDPOINT_ID": endpoint_ids["bedrock-agentcore.gateway"],
        "PROMPT4_PRIVATE_BEDROCK_RUNTIME_ENDPOINT_ID": endpoint_ids["bedrock-runtime"],
        "PROMPT4_PRIVATE_ECR_API_ENDPOINT_ID": endpoint_ids["ecr.api"],
        "PROMPT4_PRIVATE_ECR_DKR_ENDPOINT_ID": endpoint_ids["ecr.dkr"],
        "PROMPT4_PRIVATE_LOGS_ENDPOINT_ID": endpoint_ids["logs"],
        "PROMPT4_PRIVATE_S3_ENDPOINT_ID": s3_endpoint_id,
    }
    write_env_file(Path(args.env_file), values)
    result: dict[str, Any] = {
        "scenario": "private_network_lambda_runtime",
        "implemented": True,
        "ready_for_runtime_deploy": True,
        "values": values,
    }
    print(json.dumps(result, indent=2))
    for key, value in values.items():
        print(f"export {key}={value}")


if __name__ == "__main__":
    main()
