from __future__ import unicode_literals

import datetime
import os
import random
import re
import string

import boto3

from botocore.client import Config
from botocore.exceptions import ClientError

import frappe


import mimetypes


class CDNOperations(object):

    def __init__(self):
        """
        Function to initialise the aws settings from frappe CDN File attachment
        doctype.
        """
        self.cdn_settings_doc = frappe.get_doc(
            'CDN File Attachment',
            'CDN File Attachment',
        )
        # Dynamically build the endpoint URL if cdn_url is available
        endpoint_url = None
        if self.cdn_settings_doc.cdn_url:
            endpoint_url = f'https://{self.cdn_settings_doc.bucket_name}.s3.{self.cdn_settings_doc.region_name}.{self.cdn_settings_doc.cdn_url}'

        client_params = {
            'service_name': 's3',
            'endpoint_url': endpoint_url,
            'region_name': self.cdn_settings_doc.region_name,
            'config': Config(signature_version='s3v4')
        }

        if self.cdn_settings_doc.cdn_key and self.cdn_settings_doc.cdn_secret:
            client_params['aws_access_key_id'] = self.cdn_settings_doc.cdn_key
            client_params['aws_secret_access_key'] = self.cdn_settings_doc.cdn_secret

        # Remove None values from client_params
        client_params = {k: v for k, v in client_params.items() if v is not None}

        self.CDN_CLIENT = boto3.client(**client_params)
        self.BUCKET = self.cdn_settings_doc.bucket_name
        self.folder_name = self.cdn_settings_doc.folder_name


    def strip_special_chars(self, file_name):
        """
        Strips file charachters which doesnt match the regex.
        """
        regex = re.compile('[^0-9a-zA-Z._-]')
        file_name = regex.sub('', file_name)
        return file_name

    def key_generator(self, file_name, parent_doctype, parent_name):
        """
        Generate keys for s3 objects uploaded with file name attached.
        """
        hook_cmd = frappe.get_hooks().get("cdn_key_generator")
        if hook_cmd:
            try:
                k = frappe.get_attr(hook_cmd[0])(
                    file_name=file_name,
                    parent_doctype=parent_doctype,
                    parent_name=parent_name
                )
                if k:
                    return k.rstrip('/').lstrip('/')
            except:
                pass

        file_name = file_name.replace(' ', '_')
        file_name = self.strip_special_chars(file_name)
        key = ''.join(
            random.choice(
                string.ascii_uppercase + string.digits) for _ in range(8)
        )

        today = datetime.datetime.now()
        year = today.strftime("%Y")
        month = today.strftime("%m")
        day = today.strftime("%d")

        doc_path = None

        if not doc_path:
            if self.folder_name:
                final_key = self.folder_name + "/" + year + "/" + month + \
                    "/" + day + "/" + parent_doctype + "/" + key + "_" + \
                    file_name
            else:
                final_key = year + "/" + month + "/" + day + "/" + \
                    parent_doctype + "/" + key + "_" + file_name
            return final_key
        else:
            final_key = doc_path + '/' + key + "_" + file_name
            return final_key

    def upload_files_to_cdn_with_key(
            self, file_path, file_name, is_private, parent_doctype, parent_name
    ):
        """
        Uploads a new file to CDN.
        Strips the file extension to set the content_type in metadata.
        """
        mime_type, encoding = mimetypes.guess_type(file_name)
        if mime_type is None:
            mime_type = 'application/octet-stream'  # Default MIME type
        key = self.key_generator(file_name, parent_doctype, parent_name)
        content_type = mime_type
        try:
            if is_private:
                self.CDN_CLIENT.upload_file(
                    file_path, self.BUCKET, key,
                    ExtraArgs={
                        "ContentType": content_type,
                        "Metadata": {
                            "ContentType": content_type,
                            "file_name": file_name
                        }
                    }
                )
            else:
                self.CDN_CLIENT.upload_file(
                    file_path, self.BUCKET, key,
                    ExtraArgs={
                        "ContentType": content_type,
                        "ACL": 'public-read',
                        "Metadata": {
                            "ContentType": content_type,

                        }
                    }
                )

        except boto3.exceptions.S3UploadFailedError:
            frappe.throw(frappe._("File Upload Failed. Please try again."))
        return key

    def delete_from_cdn(self, key):
        """Delete file from s3"""
        self.cdn_settings_doc = frappe.get_doc(
            'CDN File Attachment',
            'CDN File Attachment',
        )

        if self.cdn_settings_doc.delete_file_from_cloud:
            try:
                self.CDN_CLIENT.delete_object(
                    Bucket=self.cdn_settings_doc.bucket_name,
                    Key=key
                )
            except ClientError:
                frappe.throw(frappe._("Access denied: Could not delete file"))


    def read_file_from_cdn(self, key):
        """
        Function to read file from a s3 file.
        """
        return self.CDN_CLIENT.get_object(Bucket=self.BUCKET, Key=key)

    def get_url(self, key, file_name=None):
        """
        Return url.

        :param bucket: s3 bucket name
        :param key: s3 object key
        """
        if self.cdn_settings_doc.signed_url_expiry_time:
            self.signed_url_expiry_time = self.cdn_settings_doc.signed_url_expiry_time # noqa
        else:
            self.signed_url_expiry_time = 120
        params = {
                'Bucket': self.BUCKET,
                'Key': key,

        }
        if file_name:
            params['ResponseContentDisposition'] = 'filename={}'.format(file_name)

        url = self.CDN_CLIENT.generate_presigned_url(
            'get_object',
            Params=params,
            ExpiresIn=self.signed_url_expiry_time,
        )

        return url


@frappe.whitelist()
def file_upload_to_cdn(doc, method):
    """
    check and upload files to s3. the path check and
    """
    cdn_upload = CDNOperations()
    path = doc.file_url
    site_path = frappe.utils.get_site_path()
    parent_doctype = doc.attached_to_doctype or 'File'
    parent_name = doc.attached_to_name
    ignore_cdn_upload_for_doctype = frappe.local.conf.get('ignore_cdn_upload_for_doctype') or ['Data Import']
    if parent_doctype not in ignore_cdn_upload_for_doctype:
        if not doc.is_private:
            file_path = site_path + '/public' + path
        else:
            file_path = site_path + path
        key = cdn_upload.upload_files_to_cdn_with_key(
            file_path, doc.file_name,
            doc.is_private, parent_doctype,
            parent_name
        )

        if doc.is_private:
            method = "frappe_cdn_attachment.controller.generate_file"
            file_url = """/api/method/{0}?key={1}&file_name={2}""".format(method, key, doc.file_name)
        else:
            file_url = '{}/{}/{}'.format(
                cdn_upload.CDN_CLIENT.meta.endpoint_url,
                cdn_upload.BUCKET,
                key
            )
        os.remove(file_path)
        frappe.db.sql("""UPDATE `tabFile` SET file_url=%s, folder=%s,
            old_parent=%s, content_hash=%s WHERE name=%s""", (
            file_url, 'Home/Attachments', 'Home/Attachments', key, doc.name))
        
        doc.file_url = file_url
        
        if parent_doctype and frappe.get_meta(parent_doctype).get('image_field'):
            frappe.db.set_value(parent_doctype, parent_name, frappe.get_meta(parent_doctype).get('image_field'), file_url)

        frappe.db.commit()


@frappe.whitelist()
def generate_file(key=None, file_name=None):
    """
    Function to stream file from s3.
    """
    if key:
        cdn_upload = CDNOperations()
        signed_url = cdn_upload.get_url(key, file_name)
        frappe.local.response["type"] = "redirect"
        frappe.local.response["location"] = signed_url
    else:
        frappe.local.response['body'] = "Key not found."
    return


def upload_existing_files_cdn(name, file_name):
    """
    Function to upload all existing files.
    """
    file_doc_name = frappe.db.get_value('File', {'name': name})
    if file_doc_name:
        doc = frappe.get_doc('File', name)
        cdn_upload = CDNOperations()
        path = doc.file_url
        site_path = frappe.utils.get_site_path()
        parent_doctype = doc.attached_to_doctype
        parent_name = doc.attached_to_name
        if not doc.is_private:
            file_path = site_path + '/public' + path
        else:
            file_path = site_path + path
        key = cdn_upload.upload_files_to_cdn_with_key(
            file_path, doc.file_name,
            doc.is_private, parent_doctype,
            parent_name
        )

        if doc.is_private:
            method = "frappe_cdn_attachment.controller.generate_file"
            file_url = """/api/method/{0}?key={1}""".format(method, key)
        else:
            file_url = '{}/{}/{}'.format(
                cdn_upload.CDN_CLIENT.meta.endpoint_url,
                cdn_upload.BUCKET,
                key
            )
        os.remove(file_path)
        doc = frappe.db.sql("""UPDATE `tabFile` SET file_url=%s, folder=%s,
            old_parent=%s, content_hash=%s WHERE name=%s""", (
            file_url, 'Home/Attachments', 'Home/Attachments', key, doc.name))
        frappe.db.commit()
    else:
        pass


def cdn_file_regex_match(file_url):
    """
    Match the public file regex match.
    """
    return re.match(
        r'^(https:|/api/method/frappe_cdn_attachment.controller.generate_file)',
        file_url
    )


@frappe.whitelist()
def migrate_existing_files():
    """
    Function to migrate the existing files to s3.
    """
    # get_all_files_from_public_folder_and_upload_to_cdn
    files_list = frappe.get_all(
        'File',
        fields=['name', 'file_url', 'file_name']
    )
    for file in files_list:
        if file['file_url']:
            if not cdn_file_regex_match(file['file_url']):
                upload_existing_files_cdn(file['name'], file['file_name'])
    return True


def delete_from_cloud(doc, method):
    """Delete file from s3"""
    s3 = CDNOperations()
    s3.delete_from_cdn(doc.content_hash)


@frappe.whitelist()
def ping():
    """
    Test function to check if api function work.
    """
    return "pong"