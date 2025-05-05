import json
import os
import ipaddress
import jwt
import time
import logging
import boto3
from jwt.exceptions import InvalidTokenError
from typing import Dict, Any, Tuple

logger = logging.getLogger()
logger.setLevel(logging.INFO)

def is_ip_allowed(ip: str, allowed_ranges: list) -> bool:
    """
    Check if IP is within allowed CIDR ranges
    """
    try:
        client_ip = ipaddress.ip_address(ip)
        for cidr in allowed_ranges:
            if client_ip in ipaddress.ip_network(cidr.strip()):
                return True
        return False
    except ValueError as e:
        logger.error(f"IP validation error: {str(e)}")
        return False

def validate_token(token: str, user_pool_id: str) -> Tuple[bool, Dict[str, Any]]:
    """
    Validate Cognito JWT token
    """
    try:
        # Get Cognito user pool keys
        region = boto3.Session().region_name
        keys_url = f'https://cognito-idp.{region}.amazonaws.com/{user_pool_id}/.well-known/jwks.json'
        headers = {'Accept': 'application/json'}
        
        client = boto3.client('cognito-idp')
        response = client.get_signing_key(
            UserPoolId=user_pool_id,
            KeyId=token.split('.')[0]  # Get key ID from token header
        )
        
        # Decode and validate token
        decoded = jwt.decode(
            token,
            key=response['Key'],
            algorithms=['RS256'],
            options={
                'verify_exp': True,
                'verify_aud': True,
                'verify_iss': True
            },
            audience=os.environ['COGNITO_CLIENT_ID'],
            issuer=f'https://cognito-idp.{region}.amazonaws.com/{user_pool_id}'
        )
        
        # Additional custom validations
        if 'sub' not in decoded:
            return False, {"error": "Invalid token: missing sub claim"}
            
        if 'token_use' not in decoded or decoded['token_use'] != 'access':
            return False, {"error": "Invalid token: not an access token"}
            
        return True, decoded
        
    except InvalidTokenError as e:
        logger.error(f"Token validation error: {str(e)}")
        return False, {"error": f"Token validation failed: {str(e)}"}
    except Exception as e:
        logger.error(f"Unexpected error during token validation: {str(e)}")
        return False, {"error": "Internal authorization error"}

def check_rate_limit(connection_id: str) -> bool:
    """
    Implement rate limiting using DynamoDB
    """
    try:
        dynamodb = boto3.client('dynamodb')
        current_time = int(time.time())
        window_size = 60  # 1 minute window
        max_requests = 100  # max requests per window
        
        response = dynamodb.update_item(
            TableName=os.environ['RATE_LIMIT_TABLE'],
            Key={
                'connection_id': {'S': connection_id},
                'window': {'N': str(current_time - (current_time % window_size))}
            },
            UpdateExpression='ADD request_count :inc',
            ExpressionAttributeValues={
                ':inc': {'N': '1'},
                ':max': {'N': str(max_requests)}
            },
            ConditionExpression='attribute_not_exists(request_count) OR request_count < :max',
            ReturnValues='UPDATED_NEW'
        )
        return True
    except dynamodb.exceptions.ConditionalCheckFailedException:
        return False
    except Exception as e:
        logger.error(f"Rate limiting error: {str(e)}")
        return True  # Allow on error

def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Main handler for WebSocket authorization
    """
    logger.info(f"Processing authorization request: {json.dumps(event)}")
    
    try:
        # Extract request details
        request_context = event.get('requestContext', {})
        connection_id = request_context.get('connectionId', '')
        source_ip = request_context.get('identity', {}).get('sourceIp', '')
        
        # Get query parameters
        query_params = event.get('queryStringParameters', {}) or {}
        token = query_params.get('token', '')
        
        # Check IP restrictions
        allowed_ranges = os.environ['ALLOWED_IP_RANGES'].split(',')
        if not is_ip_allowed(source_ip, allowed_ranges):
            logger.warning(f"IP restricted: {source_ip}")
            return {
                "isAuthorized": False,
                "context": {
                    "error": "IP address not allowed",
                    "connectionId": connection_id
                }
            }
        
        # Check rate limiting
        if not check_rate_limit(connection_id):
            logger.warning(f"Rate limit exceeded: {connection_id}")
            return {
                "isAuthorized": False,
                "context": {
                    "error": "Rate limit exceeded",
                    "connectionId": connection_id
                }
            }
        
        # Validate token
        if not token:
            logger.warning("No token provided")
            return {
                "isAuthorized": False,
                "context": {
                    "error": "No authorization token provided",
                    "connectionId": connection_id
                }
            }
        
        is_valid, token_data = validate_token(
            token,
            os.environ['USER_POOL_ID']
        )
        
        if not is_valid:
            logger.warning(f"Token validation failed: {token_data.get('error')}")
            return {
                "isAuthorized": False,
                "context": {
                    "error": token_data.get('error'),
                    "connectionId": connection_id
                }
            }
        
        # Authorization successful
        return {
            "isAuthorized": True,
            "context": {
                "userId": token_data['sub'],
                "email": token_data.get('email', ''),
                "connectionId": connection_id,
                "sourceIp": source_ip,
                "scope": token_data.get('scope', ''),
                "principalId": token_data['sub']
            }
        }
        
    except Exception as e:
        logger.error(f"Authorization error: {str(e)}")
        return {
            "isAuthorized": False,
            "context": {
                "error": "Internal authorization error",
                "connectionId": connection_id
            }
        }
