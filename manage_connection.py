import json
import os
import boto3
import time
import logging
import bleach
from datetime import datetime, timezone

logger = logging.getLogger()
logger.setLevel(logging.INFO)

dynamodb = boto3.client('dynamodb')
kinesis = boto3.client('kinesis')

def validate_message(message):
    try:
        # Check message size
        message_size = len(json.dumps(message))
        max_size = int(os.environ['MESSAGE_SIZE_LIMIT'])
        if message_size > max_size:
            return False, f"Message exceeds size limit of {max_size} bytes"

        # Validate required fields
        required_fields = ['action', 'data']
        if not all(field in message for field in required_fields):
            return False, "Missing required fields: action and data"

        # Sanitize string values
        sanitized_message = {}
        for key, value in message.items():
            if isinstance(value, str):
                sanitized_message[key] = bleach.clean(value)
            else:
                sanitized_message[key] = value

        return True, sanitized_message
    except Exception as e:
        logger.error(f"Message validation error: {str(e)}")
        return False, "Invalid message format"

def manage_connection(connection_id, event_type, user_id=None):
    try:
        current_time = int(time.time())
        
        if event_type == 'connect':
            # Check concurrent connections
            connections = dynamodb.scan(
                TableName=os.environ['CONNECTION_TABLE'],
                Select='COUNT'
            )
            if connections.get('Count', 0) >= int(os.environ['MAX_CONNECTION_COUNT']):
                return False, "Maximum connections reached"

            # Store connection data
            dynamodb.put_item(
                TableName=os.environ['CONNECTION_TABLE'],
                Item={
                    'connection_id': {'S': connection_id},
                    'user_id': {'S': user_id},
                    'connected_at': {'N': str(current_time)},
                    'ttl': {'N': str(current_time + int(os.environ['MAX_CONNECTION_DURATION']))},
                    'last_activity': {'N': str(current_time)}
                }
            )
            return True, "Connection established"

        elif event_type == 'disconnect':
            dynamodb.delete_item(
                TableName=os.environ['CONNECTION_TABLE'],
                Key={'connection_id': {'S': connection_id}}
            )
            return True, "Connection terminated"

        elif event_type == 'activity':
            dynamodb.update_item(
                TableName=os.environ['CONNECTION_TABLE'],
                Key={'connection_id': {'S': connection_id}},
                UpdateExpression='SET last_activity = :time',
                ExpressionAttributeValues={':time': {'N': str(current_time)}}
            )
            return True, "Activity recorded"

    except Exception as e:
        logger.error(f"Connection management error: {str(e)}")
        return False, "Connection management failed"
def lambda_handler(event, context):
    try:
        logger.info(f"Processing event: {json.dumps(event)}")
        connection_id = event['requestContext']['connectionId']
        route_key = event['requestContext']['routeKey']
        
        # Get user context from authorizer
        authorizer_context = event['requestContext'].get('authorizer', {})
        user_id = authorizer_context.get('userId')
        
        # Handle connection lifecycle events
        if route_key == '$connect':
            success, message = manage_connection(connection_id, 'connect', user_id)
            return {
                'statusCode': 200 if success else 400,
                'body': message
            }
            
        if route_key == '$disconnect':
            success, message = manage_connection(connection_id, 'disconnect')
            return {
                'statusCode': 200,
                'body': message
            }
        
        # Handle data messages
        try:
            body = json.loads(event.get('body', '{}'))
        except json.JSONDecodeError:
            return {
                'statusCode': 400,
                'body': json.dumps({'error': 'Invalid JSON'})
            }
        
        # Validate and sanitize message
        is_valid, result = validate_message(body)
        if not is_valid:
            return {
                'statusCode': 400,
                'body': json.dumps({'error': result})
            }
        
        # Update last activity
        manage_connection(connection_id, 'activity')
        
        # Process the action
        action = result.get('action')
        
        if action == 'getRecords':
            try:
                # Get stream details
                stream_name = os.environ['STREAM_NAME']
                stream_info = kinesis.describe_stream(StreamName=stream_name)
                shards = stream_info['StreamDescription']['Shards']
                
                all_records = []
                for shard in shards:
                    shard_iterator = kinesis.get_shard_iterator(
                        StreamName=stream_name,
                        ShardId=shard['ShardId'],
                        ShardIteratorType='TRIM_HORIZON'
                    )['ShardIterator']
                    
                    records = kinesis.get_records(
                        ShardIterator=shard_iterator,
                        Limit=100
                    )['Records']
                    
                    # Filter records for user
                    for record in records:
                        data = json.loads(record['Data'].decode('utf-8'))
                        if data.get('metadata', {}).get('userId') == user_id:
                            all_records.append({
                                'data': data,
                                'sequenceNumber': record['SequenceNumber'],
                                'partitionKey': record['PartitionKey'],
                                'timestamp': record['ApproximateArrivalTimestamp'].isoformat()
                            })
                
                return {
                    'statusCode': 200,
                    'body': json.dumps({
                        'message': 'Records retrieved',
                        'records': all_records
                    })
                }
                
            except Exception as e:
                logger.error(f"Error processing Kinesis records: {str(e)}")
                return {
                    'statusCode': 500,
                    'body': json.dumps({'error': 'Error processing records'})
                }
        
        return {
            'statusCode': 400,
            'body': json.dumps({'error': 'Invalid action'})
        }
        
    except Exception as e:
        logger.error(f"Error processing request: {str(e)}")
        return {
            'statusCode': 500,
            'body': json.dumps({'error': 'Internal server error'})
        }
