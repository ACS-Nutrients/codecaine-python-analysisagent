"""
GitHub Actions OIDC 설정 스크립트 (최초 1회 실행).

사용법:
  python scripts/setup_oidc.py \
    --github-org  YOUR_ORG \
    --github-repo YOUR_REPO

실행 후 출력된 Role ARN을
GitHub Secrets → AWS_DEPLOY_ROLE_ARN 에 등록.
"""

import argparse
import boto3
import json

ACCOUNT_ID = boto3.client("sts").get_caller_identity()["Account"]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--github-org",  required=True)
    parser.add_argument("--github-repo", required=True)
    args = parser.parse_args()

    iam = boto3.client("iam")

    # OIDC Provider 생성 (계정당 1회)
    try:
        iam.create_open_id_connect_provider(
            Url="https://token.actions.githubusercontent.com",
            ClientIDList=["sts.amazonaws.com"],
            ThumbprintList=["6938fd4d98bab03faadb97b34396831e3780aea1"],
        )
        print("✅ OIDC Provider 생성 완료")
    except iam.exceptions.EntityAlreadyExistsException:
        print("ℹ️  OIDC Provider 이미 존재")

    # Trust Policy
    trust_policy = {
        "Version": "2012-10-17",
        "Statement": [{
            "Effect": "Allow",
            "Principal": {
                "Federated": f"arn:aws:iam::{ACCOUNT_ID}:oidc-provider/token.actions.githubusercontent.com"
            },
            "Action": "sts:AssumeRoleWithWebIdentity",
            "Condition": {
                "StringEquals": {
                    "token.actions.githubusercontent.com:aud": "sts.amazonaws.com"
                },
                "StringLike": {
                    "token.actions.githubusercontent.com:sub":
                        f"repo:{args.github_org}/{args.github_repo}:*"
                }
            }
        }]
    }

    # IAM Role 생성
    role_name = "github-actions-analysis-agent"
    try:
        response = iam.create_role(
            RoleName=role_name,
            AssumeRolePolicyDocument=json.dumps(trust_policy),
            Description="GitHub Actions OIDC Role for Analysis Agent",
        )
        role_arn = response["Role"]["Arn"]
        print(f"✅ IAM Role 생성: {role_arn}")
    except iam.exceptions.EntityAlreadyExistsException:
        role_arn = f"arn:aws:iam::{ACCOUNT_ID}:role/{role_name}"
        print(f"ℹ️  IAM Role 이미 존재: {role_arn}")

    # 배포 권한 정책
    policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": [
                    "ecr:GetAuthorizationToken",
                    "ecr:BatchCheckLayerAvailability",
                    "ecr:PutImage",
                    "ecr:InitiateLayerUpload",
                    "ecr:UploadLayerPart",
                    "ecr:CompleteLayerUpload",
                ],
                "Resource": "*"
            },
            {
                "Effect": "Allow",
                "Action": [
                    "lambda:UpdateFunctionCode",
                    "lambda:GetFunction",
                ],
                "Resource": f"arn:aws:lambda:ap-northeast-2:{ACCOUNT_ID}:function:action-nutrient-calc"
            },
            {
                "Effect": "Allow",
                "Action": [
                    "bedrock-agentcore-control:UpdateAgentRuntime",
                    "bedrock-agentcore-control:GetAgentRuntime",
                ],
                "Resource": f"arn:aws:bedrock-agentcore:ap-northeast-2:{ACCOUNT_ID}:runtime/*"
            },
            {
                "Effect": "Allow",
                "Action": "iam:PassRole",
                "Resource": f"arn:aws:iam::{ACCOUNT_ID}:role/agentcore-runtime-role"
            }
        ]
    }

    iam.put_role_policy(
        RoleName=role_name,
        PolicyName="analysis-agent-deploy-policy",
        PolicyDocument=json.dumps(policy),
    )
    print("✅ IAM 정책 연결 완료")

    print("\n" + "=" * 60)
    print("GitHub Secrets에 아래 값을 등록하세요:")
    print(f"\n  AWS_DEPLOY_ROLE_ARN = {role_arn}")
    print("\n등록 경로: 레포 → Settings → Secrets and variables → Actions")
    print("=" * 60)


if __name__ == "__main__":
    main()