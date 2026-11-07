"""Shared pure creation-provenance checks. Inputs must come from authenticated immutable
artifacts; this module does not turn an arbitrary supplied dict into trustworthy evidence.
"""
import hashlib,json
from datetime import datetime,timedelta,timezone
ACCOUNT='664759038511';REGION='us-east-1';EXECUTOR=f'arn:aws:iam::{ACCOUNT}:role/BibleVerseCfnExecution';DEPLOY=f'arn:aws:iam::{ACCOUNT}:role/BibleVerseCdkDeploy'
def need(ok,message):
 if not ok:raise ValueError(message)
def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()
def utc(value):return datetime.fromisoformat(value.replace('Z','+00:00'))
def own_response(request,cs,response,origin_run):
 need(response['run_id']==origin_run,'Wrong own-response producer')
 need(response['request_sha256']==digest(request),'Own response request differs')
 result=response['response'];need(result.get('Id')==cs['ChangeSetId'] and result.get('StackId')==request['StackName'],'Own response does not bind server change set')
 need(request.get('RoleARN')==EXECUTOR,'Wrong requested executor')
 return {'source':'ImmutableOwnCreateResponse','role_arn':EXECUTOR,'change_set_arn':result['Id'],'response_artifact_id':response['_artifact_binding']['id'],'request_sha256':digest(request)}
def operator_event(request,origin,event,cs,now,session_prefix):
 params=event.get('requestParameters',{});identity=event.get('userIdentity',{});response=event.get('responseElements',{})
 need(not event.get('errorCode') and not event.get('errorMessage') and event.get('eventName')=='CreateChangeSet' and event.get('eventSource')=='cloudformation.amazonaws.com' and event.get('awsRegion')==REGION and event.get('recipientAccountId')==ACCOUNT,'Wrong operator event')
 need(identity.get('arn')==f'arn:aws:sts::{ACCOUNT}:assumed-role/BibleVerseCdkDeploy/{session_prefix}{origin["run_id"]}' and identity.get('sessionContext',{}).get('sessionIssuer',{}).get('arn')==DEPLOY,'Wrong creation session')
 need(params.get('roleARN',params.get('roleArn'))==EXECUTOR and params.get('stackName')==request['StackName'] and params.get('changeSetName')==request['ChangeSetName'] and params.get('changeSetType')=='UPDATE' and params.get('capabilities')==request.get('Capabilities') and params.get('clientToken')==request['ClientToken'] and params.get('description')==request['Description'] and params.get('templateURL',params.get('templateUrl'))==request['TemplateURL'],'Operator event request differs')
 need(response.get('id')==cs['ChangeSetId'] and response.get('stackId')==request['StackName'] and event.get('eventID') and event.get('requestID'),'Operator event response differs')
 need(utc(origin['created_at'])-timedelta(minutes=5)<=utc(event['eventTime'])<=now,'Operator event outside trusted interval')
 return {'source':'OperatorCloudTrailCreateChangeSet','role_arn':EXECUTOR,'change_set_arn':cs['ChangeSetId'],'event_id':event['eventID'],'request_sha256':digest(request)}
def executor_verified(cs):
 # A real DescribeChangeSet response normally lacks RoleARN. Never infer it from the stack.
 if 'RoleARN' in cs:need(cs['RoleARN']==EXECUTOR,'Wrong returned executor')
 proof=cs.get('ExecutorEvidence')
 need(isinstance(proof,dict) and proof.get('source') in ['ImmutableOwnCreateResponse','OperatorCloudTrailCreateChangeSet'] and proof.get('role_arn')==EXECUTOR and proof.get('change_set_arn')==cs.get('ChangeSetId') and (proof.get('event_id') or proof.get('response_artifact_id')) and proof.get('request_sha256'),'Authenticated creation executor evidence unavailable')
 return True
