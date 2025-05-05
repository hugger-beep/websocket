## Change to match your sharding ID (ShardId='shardId-000000000000'), For demo purpose.

import json
import boto3
import os
from typing import Dict, Any, Optional, List
from botocore.exceptions import ClientError
import logging
import hmac
import base64
import hashlib

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class WebSocketClient:
    def __init__(self, 
                 websocket_url: str,
                 secret_name: str,
                 region: str = 'us-east-1'):
        """
        Initialize WebSocket client with AWS configuration
        """
        self.websocket_url = websocket_url
        self.secret_name = secret_name
        self.region = region
        self.token = None
        self.connection_id = None
        
        # Initialize AWS clients
        self.secrets_client = boto3.client('secretsmanager', region_name=self.region)
        self.cognito_client = boto3.client('cognito-idp', region_name=self.region)
        self.apigateway_client = boto3.client('apigatewaymanagementapi', region_name=self.region)
        self.kinesis_client = boto3.client('kinesis', region_name=self.region)

    def calculate_secret_hash(self, username: str, client_id: str, client_secret: str) -> str:
        """
        Calculate the secret hash for Cognito authentication
        """
        message = username + client_id
        dig = hmac.new(
            key=client_secret.encode('utf-8'),
            msg=message.encode('utf-8'),
            digestmod=hashlib.sha256
        ).digest()
        return base64.b64encode(dig).decode()

    def get_secret(self) -> Optional[Dict[str, str]]:
        """
        Retrieve secret from AWS Secrets Manager
        """
        try:
            response = self.secrets_client.get_secret_value(
                SecretId=self.secret_name
            )
            
            if 'SecretString' in response:
                secret = json.loads(response['SecretString'])
                logger.info("Successfully retrieved secret")
                return secret
            else:
                logger.error("Secret not found in SecretString")
                return None
                
        except ClientError as e:
            logger.error(f"Failed to get secret: {str(e)}")
            return None

    def get_cognito_token(self) -> Optional[str]:
        """
        Authenticate with Cognito and get JWT token
        """
        try:
            # Get credentials from Secrets Manager
            credentials = self.get_secret()
            if not credentials:
                raise Exception("Failed to get credentials from Secrets Manager")
            
            # Validate required credentials
            required_fields = ['username', 'password', 'client_id']
            for field in required_fields:
                if field not in credentials:
                    raise Exception(f"Missing required field in secret: {field}")
            
            # Prepare authentication parameters
            auth_params = {
                'USERNAME': credentials['username'],
                'PASSWORD': credentials['password']
            }
            
            # Add SECRET_HASH if client_secret is provided
            if 'client_secret' in credentials:
                secret_hash = self.calculate_secret_hash(
                    username=credentials['username'],
                    client_id=credentials['client_id'],
                    client_secret=credentials['client_secret']
                )
                auth_params['SECRET_HASH'] = secret_hash
            
            # Initiate authentication
            response = self.cognito_client.initiate_auth(
                AuthFlow='USER_PASSWORD_AUTH',
                AuthParameters=auth_params,
                ClientId=credentials['client_id']
            )
            
            # Get access token
            token = response['AuthenticationResult']['AccessToken']
            logger.info("Successfully obtained Cognito token")
            return token
            
        except Exception as e:
            logger.error(f"Cognito authentication failed: {str(e)}")
            return None

    def get_kinesis_records(self, limit: int = 2) -> List[Dict[str, Any]]:
        """
        Get records from Kinesis stream
        """
        try:
            logger.info(f"Attempting to get {limit} records from Kinesis stream: {os.environ['KINESIS_STREAM_NAME']}")
            
            # Get shard iterator
            shard_response = self.kinesis_client.get_shard_iterator(
                StreamName=os.environ['KINESIS_STREAM_NAME'],
                ShardId='shardId-000000000000',  # Adjust if multiple shards
                ShardIteratorType='TRIM_HORIZON'  # Changed from TRIM_HORIZON to LATEST
            )
            logger.info("Successfully got shard iterator")
            
            # Get records
            records_response = self.kinesis_client.get_records(
                ShardIterator=shard_response['ShardIterator'],
                Limit=limit
            )
            logger.info(f"Raw Kinesis response: {json.dumps(records_response, default=str)}")
            
            # Check if we got any records
            if not records_response['Records']:
                logger.info("No records found in the Kinesis stream")
                return []
            
            # Process records
            processed_records = []
            for record in records_response['Records'][:limit]:
                try:
                    # Decode and parse the record data
                    data = json.loads(record['Data'].decode('utf-8'))
                    processed_record = {
                        'data': data,
                        'sequence_number': record['SequenceNumber'],
                        'partition_key': record['PartitionKey'],
                        'approximate_arrival_timestamp': record['ApproximateArrivalTimestamp'].isoformat()
                    }
                    processed_records.append(processed_record)
                    
                    # Log each record
                    logger.info(f"Processed record: {json.dumps(processed_record, indent=2)}")
                    
                except json.JSONDecodeError as e:
                    logger.error(f"Failed to decode record: {str(e)}")
                    logger.error(f"Raw record data: {record['Data']}")
                    continue
            
            logger.info(f"Successfully processed {len(processed_records)} records")
            return processed_records
            
        except Exception as e:
            logger.error(f"Error getting Kinesis records: {str(e)}")
            logger.error(f"Full error details: {str(e.__dict__)}")
            return []


    def send_websocket_message(self, connection_id: str, message: Dict[str, Any]) -> bool:
        """
        Send message through WebSocket connection
        """
        try:
            self.apigateway_client.post_to_connection(
                ConnectionId=connection_id,
                Data=json.dumps(message)
            )
            return True
        except ClientError as e:
            logger.error(f"Failed to send WebSocket message: {str(e)}")
            return False

def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    AWS Lambda handler for WebSocket connections
    """
    logger.info(f"Received event: {json.dumps(event)}")
    
    try:
        # Initialize client
        client = WebSocketClient(
            websocket_url=os.environ.get('WEBSOCKET_URL'),
            secret_name=os.environ.get('SECRET_NAME'),
            region=os.environ.get('AWS_DEPLOYMENT_REGION', 'us-east-1')
        )

        # Get route key from event
        route_key = event['requestContext']['routeKey']
        connection_id = event['requestContext']['connectionId']
        
        logger.info(f"Processing route: {route_key} for connection: {connection_id}")

        if route_key == '$connect':
            try:
                # Get authentication token
                logger.info("Attempting to get Cognito token")
                token = client.get_cognito_token()
                if not token:
                    logger.error("Failed to obtain authentication token")
                    return {'statusCode': 401, 'body': 'Authentication failed'}

                logger.info("Connection established successfully")
                return {'statusCode': 200, 'body': 'Connected'}
            
            except Exception as e:
                error_msg = f"Error during connection: {str(e)}"
                logger.error(error_msg)
                return {'statusCode': 500, 'body': error_msg}

        elif route_key == '$disconnect':
            logger.info(f"Client disconnected: {connection_id}")
            return {'statusCode': 200, 'body': 'Disconnected'}

        elif route_key == 'getRecords':
            try:
                logger.info("Processing getRecords request")
                
                # Get records from Kinesis
                logger.info("Attempting to retrieve records from Kinesis")
                records = client.get_kinesis_records(limit=2)
                
                # Log the results
                if not records:
                    logger.info("No records were retrieved from Kinesis")
                else:
                    logger.info(f"Retrieved {len(records)} records from Kinesis")
                    for idx, record in enumerate(records, 1):
                        logger.info(f"Record {idx}: {json.dumps(record, indent=2)}")
                
                # Prepare response
                response = {
                    'statusCode': 200,
                    'body': json.dumps({
                        'message': 'Records retrieved' if records else 'No records found',
                        'records': records,
                        'record_count': len(records)
                    })
                }
                
                # Log response
                logger.info(f"Sending response: {json.dumps(response, indent=2)}")
                
                # Attempt to send message through WebSocket
                if records:
                    try:
                        websocket_message = {
                            'message': 'New records available',
                            'records': records
                        }
                        client.send_websocket_message(connection_id, websocket_message)
                        logger.info("Successfully sent records through WebSocket")
                    except Exception as ws_error:
                        logger.error(f"Failed to send WebSocket message: {str(ws_error)}")
                
                return response

            except Exception as e:
                error_msg = f"Error processing getRecords: {str(e)}"
                logger.error(error_msg)
                return {
                    'statusCode': 500,
                    'body': json.dumps({
                        'message': error_msg,
                        'error': str(e)
                    })
                }

        else:
            error_msg = f"Unknown route: {route_key}"
            logger.error(error_msg)
            return {'statusCode': 400, 'body': f'Unknown route: {route_key}'}

    except Exception as e:
        error_msg = f"Error in lambda_handler: {str(e)}"
        logger.error(error_msg)
        logger.error(f"Full error details: {str(e.__dict__)}")
        return {'statusCode': 500, 'body': str(e)}
