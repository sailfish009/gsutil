# Copyright 2011 Google Inc.
# Copyright 2011, Nexenta Systems Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import boto
import ctypes
import errno
import gzip
import mimetypes
import os
import platform
import re
import subprocess
import sys
import tempfile
import threading
import time

from boto.gs.resumable_upload_handler import ResumableUploadHandler
from boto.s3.resumable_download_handler import ResumableDownloadHandler
from gslib.command import Command
from gslib.command import COMMAND_NAME
from gslib.command import COMMAND_NAME_ALIASES
from gslib.command import CONFIG_REQUIRED
from gslib.command import FILE_URIS_OK
from gslib.command import MAX_ARGS
from gslib.command import MIN_ARGS
from gslib.command import PROVIDER_URIS_OK
from gslib.command import SUPPORTED_SUB_ARGS
from gslib.command import URIS_START_ARG
from gslib.exception import CommandException
from gslib.help_provider import HELP_NAME
from gslib.help_provider import HELP_NAME_ALIASES
from gslib.help_provider import HELP_ONE_LINE_SUMMARY
from gslib.help_provider import HELP_TEXT
from gslib.help_provider import HelpType
from gslib.help_provider import HELP_TYPE
from gslib.util import MakeHumanReadable
from gslib.util import NO_MAX
from gslib.util import ONE_MB
from gslib.wildcard_iterator import ContainsWildcard

_detailed_help_text = ("""
<B>SYNOPSIS</B>
  gsutil cp [-a canned_acl] [-e] [-p] [-z ext1,ext2,...] src_uri dst_uri
    - or -
  gsutil cp [-a canned_acl] [-e] [-p] [-R] [-z extensions] uri... dst_uri


<B>DESCRIPTION</B>
  The gsutil cp command allows you to copy data between your local file
  system and the cloud, copy data within the cloud, and copy data between
  cloud storage providers. For example, to copy all text files from the
  local directory to a bucket you could do:

    gsutil cp *.txt gs://my_bucket

  Similarly, you can download text files from a bucket by doing:

    gsutil cp gs://my_bucket/*.txt .

  If you want to copy an entire directory tree you need to use the -R option:

    gsutil cp -R dir gs://my_bucket

  If you have a large number of files to upload you might want to use the
  gsutil -m option, to perform a parallel (multi-threaded/multi-processing)
  copy:

    gsutil -m cp -R dir gs://my_bucket


<B>HOW NAMES ARE CONSTRUCTED</B>
  When performing recursive directory copies, object names are constructed
  that mirror the source directory structure starting at the point of
  recursive processing. For example, the command:

    gsutil cp -R dir1/dir2 gs://my_bucket

  will create objects named like gs://my_bucket/dir2/a/b/c, assuming
  dir1/dir2 contains the file a/b/c.

  In contrast, copying individually named files will result in objects named
  by the final path component of the source files. For example, the command:

    gsutil cp dir1/dir2/** gs://my_bucket

  will create objects named like gs://my_bucket/c.

  The same rules apply for downloads: recursive copies of buckets and
  bucket subdirectories produce mirrored filename structure, while copying
  individually (or wildcard) named objects produce flatly named files.

  Note that in the above example the '**' wildcard matches all names
  anywhere under dir. The wildcard '*' will match just one level deep
  names. For more details see 'gsutil help wildcards'.


<B>COPYING TO/FROM SUBDIRECTORIES; DISTRIBUTING TRANSFERS ACROSS MACHINES</B>
  You can use gsutil to copy to and from subdirectories by using a command like:

    gsutil cp -R dir gs://my_bucket/data

  This will cause dir and all of its files and nested subdirectories to be
  copied under the specified destination, resulting in objects with names like
  gs://my_bucket/data/dir/a/b/c. Similarly you can download from bucket
  subdirectories by using a command like:

    gsutil cp -R gs://my_bucket/data dir

  This will cause everything nested under gs://my_bucket/data dir to be
  downloaded to files, resulting in files with names like dir/data/a/b/c.

  Copying subdirectories is useful if you want to add data to an existing
  bucket directory structure over time. It's also useful if you want
  to parallelize uploads and downloads across multiple machines (often
  reducing overall transfer time compared with simply running gsutil -m
  cp on one machine). For example, if your bucket contains this structure:

    gs://my_bucket/data/result_set_01/
    gs://my_bucket/data/result_set_02/
    ...
    gs://my_bucket/data/result_set_99/

  you could perform concurrent downloads across 3 machines by running these
  commands on each machine, respectively:

    gsutil cp -R gs://my_bucket/data/result_set_[0-3]* dir
    gsutil cp -R gs://my_bucket/data/result_set_[4-6]* dir
    gsutil cp -R gs://my_bucket/data/result_set_[7-9]* dir

  Note that dir could be a local directory on each machine, or it could
  be a directory mounted off of a shared file server; whether the latter
  performs acceptably may depend on a number of things, so we recommend
  you experiment and find out what works best for you.


<B>COPYING IN THE CLOUD AND METADATA PRESERVATION</B>
  If both the source and destination URI are cloud URIs from the same
  provider, gsutil copies data "in the cloud" (i.e., without downloading
  to and uploading from the machine where you run gsutil). In addition to
  the performance and cost advantages of doing this, copying in the cloud
  preserves metadata (like Content-Type and Cache-Control).  In contrast,
  when you download data from the cloud it ends up in a file, which has
  no associated metadata. Thus, unless you have some way to hold on to
  or re-create that metadata, downloading to a file will not retain the
  metadata.

  Note that by default, the gsutil cp command does not copy the object
  ACL to the new object, and instead will use the default bucket ACL (see
  "gsutil help setdefacl").  You can override this behavior with the -p
  option (see OPTIONS below).


<B>RESUMABLE TRANSFERS</B>
  gsutil automatically uses the Google Cloud Storage resumable upload
  feature whenever you use the cp command to upload an object that is larger
  than 1 MB. You do not need to specify any special command line options
  to make this happen. If your upload is interrupted you can restart the
  upload by running the same cp command that you ran to start the upload.

  Similarly, gsutil automatically performs resumable downloads (using HTTP
  standard Range GET operations) whenever you use the cp command to download an
  object larger than 1 MB.

  Resumable uploads and downloads store some state information in a file named
  by the file being uploaded (or object being downloaded) in ~/.gsutil. If you
  attempt to resume a transfer from a machine with a different directory, the
  transfer will start over from scratch.

  See also "gsutil help prod" for details on using resumable transfers
  in production.


<B>STREAMING TRANSFERS</B>
  Use '-' in place of src_uri or dst_uri to perform a streaming
  transfer. For example:
    long_running_computation | gsutil cp - gs://my_bucket/obj

  Streaming transfers do not support resumable uploads/downloads.


<B>OPTIONS</B>
  -a          Sets named canned_acl when uploaded objects created. See
              'gsutil help acls' for further details.

  -e          Exclude symlinks. When specified, symbolic links will not be
              copied.

  -p          Causes ACL to be preserved when copying in the cloud. Note that
              this option has performance and cost implications, because it
              is essentially performing three requests (getacl, cp, setacl).
              (The performance issue can be mitigated to some degree by
              using gsutil -m cp to cause parallel copying.)

  -R, -r      Causes directories, buckets, and bucket subdirectories to be
              copied recursively. If you neglect to use this option for
              an upload, gsutil will copy any files it finds and skip any
              directories. Similarly, neglecting to specify -R for a download
              will cause gsutil to copy any objects at the current bucket
              directory level, and skip any subdirectories.

  -t          DEPRECATED. This option used to be used to request setting
              Content-Type based on file extension and/or content, which is
              now the default behavior.  The -t option is left in place for
              now to avoid breaking existing scripts. It will be removed at
              a future date.

  -z          'txt,html' Compresses file uploads with the given extensions.
              If you are uploading a large file with compressible content,
              such as a .js, .css, or .html file, you can gzip-compress the
              file during the upload process by specifying the -z <extensions>
              option. Compressing data before upload saves on usage charges
              because you are uploading a smaller amount of data.

              When you specify the -z option, the data from your files is
              compressed before it is uploaded, but your actual files are left
              uncompressed on the local disk. The uploaded objects retain the
              original content type and name as the original files but are given
              a Content-Encoding header with the value "gzip" to indicate that
              the object data stored compressed on the Google Cloud Storage
              servers.

              The -z option is most useful in combination with Content-Type
              recognition (see "gsutil help metadata").  For example, the
              following command:

                gsutil cp -z html -a public-read cattypes.html gs://mycats

              will do all of the following:
                - Upload as the object gs://mycats/cattypes.html (cp command)
                - Set the Content-Type to text/html (based on file extension)
                - Compress the data in the file cattypes.html (-z option)
                - Set the Content-Encoding to gzip (-z option)
                - Set the ACL to public-read (-a option)
                - If a user tries to view cattypes.html in a browser, the
                  browser will know to uncompress the data based on the
                  Content-Encoding header, and to render it as HTML based on
                  the Content-Type header.
""")


class CpCommand(Command):
  """Implementation of gsutil cp command."""

  # Set default Content-Type type.
  DEFAULT_CONTENT_TYPE = 'application/octet-stream'
  DEFAULT_CONTENT_ENCODING = None
  USE_MAGICFILE = boto.config.getbool('GSUtil', 'use_magicfile', False)

  # Command specification (processed by parent class).
  command_spec = {
    # Name of command.
    COMMAND_NAME : 'cp',
    # List of command name aliases.
    COMMAND_NAME_ALIASES : ['copy'],
    # Min number of args required by this command.
    MIN_ARGS : 2,
    # Max number of args required by this command, or NO_MAX.
    MAX_ARGS : NO_MAX,
    # Getopt-style string specifying acceptable sub args.
    # -t is deprecated but leave intact for now to avoid breakage.
    SUPPORTED_SUB_ARGS : 'a:eMprRtz:',
    # True if file URIs acceptable for this command.
    FILE_URIS_OK : True,
    # True if provider-only URIs acceptable for this command.
    PROVIDER_URIS_OK : False,
    # Index in args of first URI arg.
    URIS_START_ARG : 0,
    # True if must configure gsutil before running command.
    CONFIG_REQUIRED : True,
  }
  help_spec = {
    # Name of command or auxiliary help info for which this help applies.
    HELP_NAME : 'cp',
    # List of help name aliases.
    HELP_NAME_ALIASES : ['copy'],
    # Type of help:
    HELP_TYPE : HelpType.COMMAND_HELP,
    # One line summary of this help.
    HELP_ONE_LINE_SUMMARY : 'Copy files/objects to/from the cloud',
    # The full help text.
    HELP_TEXT : _detailed_help_text,
  }

  def _CheckFinalMd5(self, key, file_name):
    """
    Checks that etag from server agrees with md5 computed after the
    download completes. This is important, since the download could
    have spanned a number of hours and multiple processes (e.g.,
    gsutil runs), and the user could change some of the file and not
    realize they have inconsistent data.
    """
    # Open file in binary mode to avoid surprises in Windows.
    fp = open(file_name, 'rb')
    try:
      file_md5 = key.compute_md5(fp)[0]
    finally:
      fp.close()
    obj_md5 = key.etag.strip('"\'')
    if self.debug:
      print 'Checking file md5 against etag. (%s/%s)' % (file_md5, obj_md5)
    if file_md5 != obj_md5:
      # Checksums don't match - remove file and raise exception.
      os.unlink(file_name)
      raise CommandException(
        'File changed during download: md5 signature doesn\'t match '
        'etag (incorrect downloaded file deleted)')

  def _CheckForDirFileConflict(self, exp_src_uri, dst_uri):
    """Checks whether copying exp_src_uri into dst_uri is not possible.

       This happens if a directory exists in local file system where a file
       needs to go or vice versa. In that case we print an error message and
       exits. Example: if the file "./x" exists and you try to do:
         gsutil cp gs://mybucket/x/y .
       the request can't succeed because it requires a directory where
       the file x exists.

       Note that we don't enforce any corresponding restrictions for buckets,
       because the flat namespace semantics for buckets doesn't prohibit such
       cases the way hierarchical file systems do. For example, if a bucket
       contains an object called gs://bucket/dir and then you run the command:
         gsutil cp file1 file2 gs://bucket/dir
       you'll end up with objects gs://bucket/dir, gs://bucket/dir/file1, and
       gs://bucket/dir/file2.

    Args:
      exp_src_uri: Expanded source StorageUri of copy.
      dst_uri: Destination URI.

    Raises:
      CommandException: if errors encountered.
    """
    if dst_uri.is_cloud_uri():
      # The problem can only happen for file destination URIs.
      return
    dst_path = dst_uri.object_name
    final_dir = os.path.dirname(dst_path)
    if os.path.isfile(final_dir):
      raise CommandException('Cannot retrieve %s because a file exists '
                             'where a directory needs to be created (%s).' %
                             (exp_src_uri, final_dir))
    if os.path.isdir(dst_path):
      raise CommandException('Cannot retrieve %s because a directory exists '
                             '(%s) where the file needs to be created.' %
                             (exp_src_uri, dst_path))

  def _InsistDstUriNamesContainer(self, uri, have_multiple_srcs, command_name):
    """
    Raises an exception if URI doesn't name a directory, bucket, or bucket
    subdir.

    Args:
      uri: StorageUri to check.
      have_multiple_srcs: Bool indicator of whether operation is multi-source.
      command_name: Name of command making call. May not be the same as
          self.command_name in the case of commands implemented atop other
          commands (like mv command).

    Raises:
      CommandException: if the URI being checked does not name a container.
    """
    if ((uri.names_file() and os.path.exists(uri.object_name))
        or (uri.names_object() and not self.recursion_requested)):
      raise CommandException('Destination URI must name a directory, bucket, '
                             'or bucket\nsubdirectory for the multiple source '
                             'form of the %s command.' % command_name)

  class _FileCopyCallbackHandler(object):
    """Outputs progress info for large copy requests."""

    def __init__(self, upload):
      if upload:
        self.announce_text = 'Uploading'
      else:
        self.announce_text = 'Downloading'

    def call(self, total_bytes_transferred, total_size):
      sys.stderr.write('%s: %s/%s    \r' % (
          self.announce_text,
          MakeHumanReadable(total_bytes_transferred),
          MakeHumanReadable(total_size)))
      if total_bytes_transferred == total_size:
        sys.stderr.write('\n')

  class _StreamCopyCallbackHandler(object):
    """Outputs progress info for Stream copy to cloud.
       Total Size of the stream is not known, so we output
       only the bytes transferred.
    """

    def call(self, total_bytes_transferred, total_size):
      sys.stderr.write('Uploading: %s    \r' % (
          MakeHumanReadable(total_bytes_transferred)))
      if total_size and total_bytes_transferred == total_size:
        sys.stderr.write('\n')

  def _GetTransferHandlers(self, uri, key, file_size, upload):
    """
    Selects upload/download and callback handlers.

    We use a callback handler that shows a simple textual progress indicator
    if file_size is above the configurable threshold.

    We use a resumable transfer handler if file_size is >= the configurable
    threshold and resumable transfers are supported by the given provider.
    boto supports resumable downloads for all providers, but resumable
    uploads are currently only supported by GS.
    """
    config = boto.config
    resumable_threshold = config.getint('GSUtil', 'resumable_threshold', ONE_MB)
    if file_size >= resumable_threshold:
      cb = self._FileCopyCallbackHandler(upload).call
      num_cb = int(file_size / ONE_MB)
      resumable_tracker_dir = config.get(
          'GSUtil', 'resumable_tracker_dir',
          os.path.expanduser('~' + os.sep + '.gsutil'))
      if not os.path.exists(resumable_tracker_dir):
        os.makedirs(resumable_tracker_dir)
      if upload:
        # Encode the src bucket and key into the tracker file name.
        res_tracker_file_name = (
            re.sub('[/\\\\]', '_', 'resumable_upload__%s__%s.url' %
                   (key.bucket.name, key.name)))
      else:
        # Encode the fully-qualified src file name into the tracker file name.
        res_tracker_file_name = (
            re.sub('[/\\\\]', '_', 'resumable_download__%s.etag' %
                   (os.path.realpath(uri.object_name))))
      tracker_file = '%s%s%s' % (resumable_tracker_dir, os.sep,
                                 res_tracker_file_name)
      if upload:
        if uri.scheme == 'gs':
          transfer_handler = ResumableUploadHandler(tracker_file)
        else:
          transfer_handler = None
      else:
        transfer_handler = ResumableDownloadHandler(tracker_file)
    else:
      transfer_handler = None
      cb = None
      num_cb = None
    return (cb, num_cb, transfer_handler)

  # We pass the headers explicitly to this call instead of using self.headers
  # so we can set different metadata (like Content-Type type) for each object.
  def _CopyObjToObjSameProvider(self, src_key, src_uri, dst_uri, headers):
    # Do Object -> object copy within same provider (uses
    # x-<provider>-copy-source metadata HTTP header to request copying at the
    # server).
    src_bucket = src_uri.get_bucket(False, headers)
    dst_bucket = dst_uri.get_bucket(False, headers)
    preserve_acl = False
    if self.sub_opts:
      for o, a in self.sub_opts:
        if o == '-p':
          preserve_acl = True
    start_time = time.time()
    # Pass headers in headers param not metadata param, so boto will copy
    # existing key's metadata and just set the additional headers specified
    # in the headers param (rather than using the headers to override existing
    # metadata). In particular this allows us to copy the existing key's
    # Content-Type and other metadata users need while still being able to
    # set headers the API needs (like x-goog-project-id).
    dst_bucket.copy_key(dst_uri.object_name, src_bucket.name,
                        src_uri.object_name, preserve_acl=preserve_acl,
                        headers=headers)
    end_time = time.time()
    return (end_time - start_time, src_key.size)

  def _CheckFreeSpace(self, path):
    """Return path/drive free space (in bytes)."""
    if platform.system() == 'Windows':
      free_bytes = ctypes.c_ulonglong(0)
      ctypes.windll.kernel32.GetDiskFreeSpaceExW(ctypes.c_wchar_p(path), None,
                                                 None,
                                                 ctypes.pointer(free_bytes))
      return free_bytes.value
    else:
      (_, f_frsize, _, _, f_bavail, _, _, _, _, _) = os.statvfs(path)
      return f_frsize * f_bavail

  def _PerformResumableUploadIfApplies(self, fp, dst_uri, canned_acl, headers):
    """
    Performs resumable upload if supported by provider and file is above
    threshold, else performs non-resumable upload.

    Returns (elapsed_time, bytes_transferred).
    """
    start_time = time.time()
    file_size = os.path.getsize(fp.name)
    dst_key = dst_uri.new_key(False, headers)
    (cb, num_cb, res_upload_handler) = self._GetTransferHandlers(
        dst_uri, dst_key, file_size, True)
    if dst_uri.scheme == 'gs':
      # Resumable upload protocol is Google Cloud Storage-specific.
      dst_key.set_contents_from_file(fp, headers, policy=canned_acl,
                                     cb=cb, num_cb=num_cb,
                                     res_upload_handler=res_upload_handler)
    else:
      dst_key.set_contents_from_file(fp, headers, policy=canned_acl,
                                     cb=cb, num_cb=num_cb)
    if res_upload_handler:
      bytes_transferred = file_size - res_upload_handler.upload_start_point
    else:
      bytes_transferred = file_size
    end_time = time.time()
    return (end_time - start_time, bytes_transferred)

  def _PerformStreamUpload(self, fp, dst_uri, headers, canned_acl=None):
    """
    Performs Stream upload to cloud.

    Args:
      fp: The file whose contents to upload.
      dst_uri: Destination StorageUri.
      headers: A copy of the headers dictionary.
      canned_acl: Optional canned ACL to set on the object.

    Returns (elapsed_time, bytes_transferred).
    """
    start_time = time.time()
    dst_key = dst_uri.new_key(False, headers)

    cb = self._StreamCopyCallbackHandler().call
    dst_key.set_contents_from_stream(fp, headers, policy=canned_acl, cb=cb)
    try:
      bytes_transferred = fp.tell()
    except:
      bytes_transferred = 0

    end_time = time.time()
    return (end_time - start_time, bytes_transferred)

  def _GetContentTypeAndEncoding(self, object_name):
    # Streams (denoted by '-') are expected to be 'application/octet-stream'
    # and 'file' would partially consume them.
    if not object_name == '-':
      if self.USE_MAGICFILE:
        p = subprocess.Popen(['file', '--mime-type', object_name],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        output, error = p.communicate()
        if p.returncode != 0 or error:
          raise CommandException(
              'Encountered error running "file --mime-type %s" (returncode=%d).'
              '\n%s' % (object_name, p.returncode, error))
        # Parse output by removing line delimiter and splitting on last ": ".
        mime_type = output.rstrip().rpartition(': ')[2]
        if mime_type:
          return (mime_type, self.DEFAULT_CONTENT_ENCODING)
      else:
        return mimetypes.guess_type(object_name)
    return (self.DEFAULT_CONTENT_TYPE, self.DEFAULT_CONTENT_ENCODING)

  def _UploadFileToObject(self, src_key, src_uri, dst_uri, headers):
    """Helper method for uploading a local file to an object.

    Args:
      src_key: Source StorageUri. Must be a file URI.
      src_uri: Source StorageUri.
      dst_uri: Destination StorageUri.
      headers: The headers dictionary.
    Returns:
      (elapsed_time, bytes_transferred) excluding overhead like initial HEAD.

    Raises:
      CommandException: if errors encountered.
    """
    gzip_exts = []
    canned_acl = None
    # Previously, the -t option was used to request automatic content
    # type detection, however, whether -t was specified for not, content
    # detection was being done. To repair this problem while preserving
    # backward compatibilty, the -t option has been deprecated and content
    # type detection is now enabled by default unless the Content-Type
    # header is explicitly specified via the -h option.
    if self.sub_opts:
      for o, a in self.sub_opts:
        if o == '-a':
          canned_acls = dst_uri.canned_acls()
          if a not in canned_acls:
            raise CommandException('Invalid canned ACL "%s".' % a)
          canned_acl = a
        elif o == '-t':
          print 'Warning: -t is deprecated. Content type detection is ' + (
                'enabled by default,\nunless inhibited by specifying ') + (
                'a Content-Type header via the -h option.')
        elif o == '-z':
          gzip_exts = a.split(',')

    if 'Content-Type' in headers:
      # Process Content-Type header. If specified via -h option with empty
      # string (i.e. -h "Content-Type:") set header to None, which will
      # inhibit boto from sending the CT header. Otherwise, boto will pass
      # through the user specified CT header.
      if not headers['Content-Type']:
        headers['Content-Type'] = None
    else:
      # If no CT header was specified via the -h option, we do auto-content
      # detection and use the results to formulate the Content-Type and
      # Content-Encoding headers.
      (mime_type, content_encoding) = (
          self._GetContentTypeAndEncoding(src_uri.object_name))
      if mime_type:
        headers['Content-Type'] = mime_type
        print '\t[Setting Content-Type=%s]' % mime_type
      else:
        print '\t[Unknown content type -> using %s]' % self.DEFAULT_CONTENT_TYPE
      if content_encoding:
        headers['Content-Encoding'] = content_encoding

    fname_parts = src_uri.object_name.split('.')
    if len(fname_parts) > 1 and fname_parts[-1] in gzip_exts:
      if self.debug:
        print 'Compressing %s (to tmp)...' % src_key
      gzip_tmp = tempfile.mkstemp()
      gzip_path = gzip_tmp[1]
      # Check for temp space. Assume the compressed object is at most 2x
      # the size of the object (normally should compress to smaller than
      # the object)
      if self._CheckFreeSpace(gzip_path) < 2*int(os.path.getsize(src_key.name)):
        raise CommandException('Inadequate temp space available to compress '
                               '%s' % src_key.name)
      gzip_fp = gzip.open(gzip_path, 'wb')
      try:
        gzip_fp.writelines(src_key.fp)
      finally:
        gzip_fp.close()
      headers['Content-Encoding'] = 'gzip'
      gzip_fp = open(gzip_path, 'rb')
      try:
        (elapsed_time, bytes_transferred) = (
            self._PerformResumableUploadIfApplies(gzip_fp, dst_uri,
                                                  canned_acl, headers))
      finally:
        gzip_fp.close()
      os.unlink(gzip_path)
    elif (src_key.is_stream()
          and dst_uri.get_provider().supports_chunked_transfer()):
      (elapsed_time, bytes_transferred) = self._PerformStreamUpload(
          src_key.fp, dst_uri, headers, canned_acl)
    else:
      if src_key.is_stream():
        # For Providers that doesn't support chunked Transfers
        tmp = tempfile.NamedTemporaryFile()
        file_uri = self.suri_builder.StorageUri('file://%s' % tmp.name)
        try:
          file_uri.new_key(False, headers).set_contents_from_file(
              src_key.fp, headers)
          src_key = file_uri.get_key()
        finally:
          file_uri.close()
      try:
        (elapsed_time, bytes_transferred) = (
            self._PerformResumableUploadIfApplies(src_key.fp, dst_uri,
                                                  canned_acl, headers))
      finally:
        if src_key.is_stream():
          tmp.close()
        else:
          src_key.close()

    return (elapsed_time, bytes_transferred)

  def _DownloadObjectToFile(self, src_key, src_uri, dst_uri, headers):
    (cb, num_cb, res_download_handler) = self._GetTransferHandlers(
        src_uri, src_key, src_key.size, False)
    file_name = dst_uri.object_name
    dir_name = os.path.dirname(file_name)
    if dir_name and not os.path.exists(dir_name):
      # Do dir creation in try block so can ignore case where dir already
      # exists. This is needed to avoid a race condition when running gsutil
      # -m cp.
      try:
        os.makedirs(dir_name)
      except OSError, e:
        if e.errno != errno.EEXIST:
          raise
    # For gzipped objects not named *.gz download to a temp file and unzip.
    if (hasattr(src_key, 'content_encoding')
        and src_key.content_encoding == 'gzip'
        and not file_name.endswith('.gz')):
      # We can't use tempfile.mkstemp() here because we need a predictable
      # filename for resumable downloads.
      download_file_name = '%s_.gztmp' % file_name
      need_to_unzip = True
    else:
      download_file_name = file_name
      need_to_unzip = False
    fp = None
    try:
      if res_download_handler:
        fp = open(download_file_name, 'ab')
      else:
        fp = open(download_file_name, 'wb')
      start_time = time.time()
      src_key.get_contents_to_file(fp, headers, cb=cb, num_cb=num_cb,
                                   res_download_handler=res_download_handler)
      # If a custom test method is defined, call it here. For the copy command,
      # test methods are expected to take one argument: an open file pointer,
      # and are used to perturb the open file during download to exercise
      # download error detection.
      if self.test_method:
        self.test_method(fp)
      end_time = time.time()
    finally:
      if fp:
        fp.close()

    # Verify downloaded file checksum matched source object's checksum.
    self._CheckFinalMd5(src_key, download_file_name)

    if res_download_handler:
      bytes_transferred = (
          src_key.size - res_download_handler.download_start_point)
    else:
      bytes_transferred = src_key.size
    if need_to_unzip:
      if self.debug:
        sys.stderr.write('Uncompressing tmp to %s...\n' % file_name)
      # Downloaded gzipped file to a filename w/o .gz extension, so unzip.
      f_in = gzip.open(download_file_name, 'rb')
      f_out = open(file_name, 'wb')
      try:
        f_out.writelines(f_in)
      finally:
        f_out.close()
        f_in.close()
        os.unlink(download_file_name)
    return (end_time - start_time, bytes_transferred)

  def _PerformDownloadToStream(self, src_key, src_uri, str_fp, headers):
    (cb, num_cb, res_download_handler) = self._GetTransferHandlers(
                                src_uri, src_key, src_key.size, False)
    start_time = time.time()
    src_key.get_contents_to_file(str_fp, headers, cb=cb, num_cb=num_cb)
    end_time = time.time()
    bytes_transferred = src_key.size
    end_time = time.time()
    return (end_time - start_time, bytes_transferred)

  def _CopyFileToFile(self, src_key, dst_uri, headers):
    dst_key = dst_uri.new_key(False, headers)
    start_time = time.time()
    dst_key.set_contents_from_file(src_key.fp, headers)
    end_time = time.time()
    return (end_time - start_time, os.path.getsize(src_key.fp.name))

  def _CopyObjToObjDiffProvider(self, src_key, src_uri, dst_uri, headers):
    # If destination is GS, We can avoid the local copying through a local file
    # as GS supports chunked transfer.
    if dst_uri.scheme == 'gs':
      canned_acls = None
      if self.sub_opts:
        for o, a in self.sub_opts:
          if o == '-a':
            canned_acls = dst_uri.canned_acls()
            if a not in canned_acls:
              raise CommandException('Invalid canned ACL "%s".' % a)
            canned_acl = a
          elif o == '-p':
            # We don't attempt to preserve ACLs across providers because
            # GCS and S3 support different ACLs.
            raise NotImplementedError('Cross-provider cp -p not supported')
          elif o == '-t':
            (mime_type, content_encoding) = (
                self._GetContentTypeAndEncoding(src_uri.object_name))
            if mime_type:
              headers['Content-Type'] = mime_type
              print '\t[Setting Content-Type=%s]' % mime_type
            else:
              print '\t[Unknown content type -> using application/octet stream]'
            if content_encoding:
              headers['Content-Encoding'] = content_encoding

      # TODO: This _PerformStreamUpload call passes in a Key for fp
      # param, relying on Python "duck typing" (the fact that the lower-level
      # methods that expect an fp only happen to call fp methods that are
      # defined and semantically equivalent to those defined on src_key). This
      # should be replaced by a class that wraps an fp interface around the
      # Key, throwing 'not implemented' for methods (like seek) that aren't
      # implemented by non-file Keys.
      return self._PerformStreamUpload(src_key, dst_uri, headers, canned_acls)

    # If destination is not GS, We implement object copy through a local
    # temp file. Note that a downside of this approach is that killing the
    # gsutil process partway through and then restarting will always repeat the
    # download and upload, because the temp file name is different for each
    # incarnation. (If however you just leave the process running and failures
    # happen along the way, they will continue to restart and make progress
    # as long as not too many failures happen in a row with no progress.)
    tmp = tempfile.NamedTemporaryFile()
    if self._CheckFreeSpace(tempfile.tempdir) < src_key.size:
      raise CommandException('Inadequate temp space available to perform the '
                             'requested copy')
    start_time = time.time()
    file_uri = self.suri_builder.StorageUri('file://%s' % tmp.name)
    try:
      self._DownloadObjectToFile(src_key, src_uri, file_uri, headers)
      self._UploadFileToObject(file_uri.get_key(), file_uri, dst_uri, headers)
    finally:
      tmp.close()
    end_time = time.time()
    return (end_time - start_time, src_key.size)

  def _PerformCopy(self, src_uri, dst_uri):
    """Performs copy from src_uri to dst_uri, handling various special cases.

    Args:
      src_uri: Source StorageUri.
      dst_uri: Destination StorageUri.

    Returns:
      (elapsed_time, bytes_transferred) excluding overhead like initial HEAD.

    Raises:
      CommandException: if errors encountered.
    """
    # Make a copy of the input headers each time so we can set a different
    # MIME type for each object.
    if self.headers:
      headers = self.headers.copy()
    else:
      headers = {}

    src_key = src_uri.get_key(False, headers)
    if not src_key:
      raise CommandException('"%s" does not exist.' % src_uri)

    # Separately handle cases to avoid extra file and network copying of
    # potentially very large files/objects.

    if src_uri.is_cloud_uri() and dst_uri.is_cloud_uri():
      if src_uri.scheme == dst_uri.scheme:
        return self._CopyObjToObjSameProvider(src_key, src_uri, dst_uri,
                                              headers)
      else:
        return self._CopyObjToObjDiffProvider(src_key, src_uri, dst_uri,
                                              headers)
    elif src_uri.is_file_uri() and dst_uri.is_cloud_uri():
      return self._UploadFileToObject(src_key, src_uri, dst_uri, headers)
    elif src_uri.is_cloud_uri() and dst_uri.is_file_uri():
      return self._DownloadObjectToFile(src_key, src_uri, dst_uri, headers)
    elif src_uri.is_file_uri() and dst_uri.is_file_uri():
      return self._CopyFileToFile(src_key, dst_uri, headers)
    else:
      raise CommandException('Unexpected src/dest case')

  def _ExpandDstUri(self, src_uri_expansion, dst_uri_str):
    """
    Expands the destination URI (e.g., expanding wildcard-named destination
    bucket). The final destination URI will be constructed from this URI
    based on each individual object, file, directory, bucket, or directory
    sub bucket being copied to it).

    Args:
      src_uri_expansion: gslib.name_expansion.NameExpansionResult.
      dst_uri_str: String representation of requested dst_uri.

    Returns:
        Expanded StorageUri.

    Raises:
      CommandException: if dst_uri_str matched more than 1 URI.
    """
    if ContainsWildcard(dst_uri_str):
      matched_uris = list(
          self.exp_handler.WildcardIterator(dst_uri_str).IterUris())
      if len(matched_uris) != 1:
        raise CommandException('Destination (%s) must match exactly 1 URI' %
                               dst_uri_str)
      return matched_uris[0]
    else:
      return self.suri_builder.StorageUri(dst_uri_str)

  def _ConstructDstUri(self, src_uri, exp_src_uri,
                       src_uri_names_container, src_uri_expands_to_multi,
                       have_multiple_srcs, exp_dst_uri):
    """
    Constructs the destination URI for a given exp_src_uri/exp_dst_uri pair,
    using context-dependent naming rules intended to mimic UNIX cp semantics.

    Args:
      src_uri: src_uri to be copied.
      exp_src_uri: Single StorageUri from wildcard expansion of src_uri.
      src_uri_names_container: True if src_uri names a container (including the
          case of a wildcard-named bucket subdir (like gs://bucket/abc,
          where gs://bucket/abc/* matched some objects). Note that this is
          additional semantics tha src_uri.names_container() doesn't understand
          because the latter only understands StorageUris, not wildcards.
      src_uri_expands_to_multi: True if src_uri expanded to multiple URIs.
      have_multiple_srcs: True if this is a multi-source request. This can be
          true if src_uri wildcard-expanded to multiple URIs or if there were
          multiple source URIs in the request.
      exp_dst_uri: the expanded StorageUri requested for the cp destination.
          Final written path is constructed from this plus a context-dependent
          variant of src_uri.

    Returns:
      StorageUri to use for copy.

    Raises:
      CommandException if destination object name not specified for
      source and source is a stream.
    """
    if self._ShouldTreatDstUriAsSingleton(have_multiple_srcs, exp_dst_uri):
      # We're copying one file or object to one file or object.
      return exp_dst_uri

    # Else we're copying multiple sources to a directory, bucket, or a bucket
    # "sub-directory".

    # Ensure exp_dst_uri ends in delim char if we're doing a multi-src copy or
    # a copy to a directory. (The check for copying to a directory needs
    # special-case handling so that the command:
    #   gsutil cp gs://bucket/obj dir
    # will turn into file://dir/ instead of file://dir -- the latter would cause
    # the file "dirobj" to be created.)
    # Note: need to check have_multiple_srcs or src_uri.names_container()
    # because src_uri could be a bucket containing a single object, named
    # as gs://bucket.
    if ((have_multiple_srcs or src_uri.names_container()
         or os.path.isdir(exp_dst_uri.object_name))
        and not exp_dst_uri.uri.endswith(exp_dst_uri.delim)):
      exp_dst_uri = exp_dst_uri.clone_replace_name(
         '%s%s' % (exp_dst_uri.object_name, exp_dst_uri.delim)
      )

    # There are 3 cases for copying multiple sources to a dir/bucket/bucket
    # subdir needed to match the naming semantics of the UNIX cp command:
    # 1. For the "mv -R" command, people expect renaming to occur at the
    #    level of the src subdir, vs appending that subdir beneath
    #    the dst subdir like is done for copying. For example:
    #      gsutil -m rm -R gs://bucket
    #      gsutil -m cp -R cloudreader gs://bucket
    #      gsutil -m cp -R cloudauth gs://bucket/subdir1
    #      gsutil -m mv -R gs://bucket/subdir1 gs://bucket/subdir2
    #    would (if using cp semantics) end up with paths like:
    #      gs://bucket/subdir2/subdir1/cloudauth/.svn/all-wcprops
    #    whereas people expect:
    #      gs://bucket/subdir2/cloudauth/.svn/all-wcprops
    # 2. Copying from directories, buckets, or bucket subdirs should result in
    #    objects/files mirroring the source directory hierarchy. Example:
    #      gsutil cp dir1/dir2 gs://bucket
    #    should create the object gs://bucket/dir2/file2, assuming dir1/dir2
    #    contains file2).
    # 3. Copying individual files or objects to dirs, buckets or bucket subdirs
    #    should result in objects/files named by the final source file name
    #    component. Example:
    #      gsutil cp dir1/*.txt gs://bucket
    #    should create the objects gs://bucket/f1.txt and gs://bucket/f2.txt,
    #    assuming dir1 contains f1.txt and f2.txt.

    if (self.mv_naming_semantics and self.recursion_requested
        and src_uri_expands_to_multi):
      # Case 1. Handle naming semantics for recursive bucket subdir mv.
      # Here we want to line up the src_uri against its expansion, to find
      # the base to build the new name. For example, starting with:
      #   gsutil mv -R gs://bucket/abcd gs://bucket/xyz
      # and exp_src_uri being gs://bucket/abcd/123
      # we want exp_src_uri_tail to be /123
      # Note: mv.py code disallows wildcard specification of source URI.
      exp_src_uri_tail = exp_src_uri.uri[len(src_uri.uri):]
      dst_key_name = '%s/%s' % (exp_dst_uri.object_name.rstrip('/'),
                                exp_src_uri_tail.strip('/'))
      return exp_dst_uri.clone_replace_name(dst_key_name)

    if src_uri_names_container and not exp_dst_uri.names_file():
      # Case 2. Build dst_key_name from subpath of exp_src_uri past
      # where src_uri ends. For example, for src_uri=gs://bucket/ and
      # exp_src_uri=gs://bucket/src_subdir/obj, dst_key_name should be
      # src_subdir/obj.
      src_uri_path_sans_final_dir = _GetPathBeforeFinalDir(src_uri)
      dst_key_name = exp_src_uri.uri[
         len(src_uri_path_sans_final_dir):].lstrip(src_uri.delim)
      # Handle special case where src_uri was a directory named with '.' or
      # './', so that running a command like:
      #   gsutil cp -r . gs://dest
      # will produce obj names of the form gs://dest/abc instead of
      # gs://dest/./abc.
      if dst_key_name.startswith('./'):
        dst_key_name = dst_key_name[2:]

    else:
      # Case 3.
      if exp_src_uri.is_stream():
        raise CommandException('Destination object name needed when '
                               'source is a stream')
      dst_key_name = exp_src_uri.object_name.rpartition(src_uri.delim)[-1]

    if (exp_dst_uri.is_file_uri()
        or self._ShouldTreatDstUriAsBucketSubDir(
            have_multiple_srcs, exp_dst_uri)):
      dst_key_name = '%s%s' % (exp_dst_uri.object_name, dst_key_name)

    return exp_dst_uri.clone_replace_name(dst_key_name)

  # Command entry point.
  def RunCommand(self):

    # Inner funcs.
    def _CopyExceptionHandler(e):
      """Simple exception handler to allow post-completion status."""
      self.THREADED_LOGGER.error(str(e))
      self.copy_failure_count += 1

    def _CopyFunc(src_uri, exp_src_uri, src_uri_names_container,
                  src_uri_expands_to_multi, have_multiple_srcs):
      """Worker function for performing the actual copy."""
      if exp_src_uri.is_file_uri() and exp_src_uri.is_stream():
        sys.stderr.write("Copying from <STDIN>...\n")
      else:
        self.THREADED_LOGGER.info('Copying %s...', exp_src_uri)
      dst_uri = self._ConstructDstUri(src_uri, exp_src_uri,
                                      src_uri_names_container,
                                      src_uri_expands_to_multi,
                                      have_multiple_srcs, exp_dst_uri)

      self._CheckForDirFileConflict(exp_src_uri, dst_uri)
      if self._SrcDstSame(exp_src_uri, dst_uri):
        raise CommandException('cp: "%s" and "%s" are the same file - '
                               'abort.' % (exp_src_uri, dst_uri))

      (elapsed_time, bytes_transferred) = self._PerformCopy(exp_src_uri,
                                                            dst_uri)
      stats_lock.acquire()
      self.total_elapsed_time += elapsed_time
      self.total_bytes_transferred += bytes_transferred
      stats_lock.release()

    # Start of RunCommand code.
    self._ParseArgs()

    self.total_elapsed_time = self.total_bytes_transferred = 0
    if self.args[-1] == '-' or self.args[-1] == 'file://-':
      self._HandleStreamingDownload()
      return

    src_uri_expansion = self.exp_handler.ExpandWildcardsAndContainers(
        self.args[0:len(self.args)-1])
    exp_dst_uri = self._ExpandDstUri(src_uri_expansion, self.args[-1])

    self._SanityCheckRequest(src_uri_expansion, exp_dst_uri)

    # Use a lock to ensure accurate statistics in the face of
    # multi-threading/multi-processing.
    stats_lock = threading.Lock()

    # Tracks if any copies failed.
    self.copy_failure_count = 0

    # Start the clock.
    start_time = time.time()

    # Tuple of attributes to share/manage across multiple processes in
    # parallel (-m) mode.
    shared_attrs = ('copy_failure_count', 'total_bytes_transferred')

    # Perform copy requests in parallel (-m) mode, if requested, using
    # configured number of parallel processes and threads. Otherwise,
    # perform request with sequential function calls in current process.
    self.Apply(_CopyFunc, src_uri_expansion, _CopyExceptionHandler,
               shared_attrs)
    if self.debug:
      print 'total_bytes_transferred:' + str(self.total_bytes_transferred)

    end_time = time.time()
    self.total_elapsed_time = end_time - start_time

    if self.debug == 3:
      # Note that this only counts the actual GET and PUT bytes for the copy
      # - not any transfers for doing wildcard expansion, the initial HEAD
      # request boto performs when doing a bucket.get_key() operation, etc.
      if self.total_bytes_transferred != 0:
        sys.stderr.write(
            'Total bytes copied=%d, total elapsed time=%5.3f secs (%sps)\n' % (
                self.total_bytes_transferred, self.total_elapsed_time,
                MakeHumanReadable(float(self.total_bytes_transferred) /
                                  float(self.total_elapsed_time))))
    if self.copy_failure_count:
      plural_str = ''
      if self.copy_failure_count > 1:
        plural_str = 's'
      raise CommandException('%d file%s/object%s could not be transferred.' % (
                             self.copy_failure_count, plural_str, plural_str))

  # test specification, see definition of test_steps in base class for
  # details on how to populate these fields
  test_steps = [
    # (test name, cmd line, ret code, (result_file, expect_file))
    ('upload', 'gsutil cp $F1 gs://$B1/$O1', 0, None),
    ('download', 'gsutil cp gs://$B1/$O1 $F9', 0, ('$F9', '$F1')),
    ('stream upload', 'cat $F1 | gsutil cp - gs://$B1/$O1', 0, None),
    ('check stream upload', 'gsutil cp gs://$B1/$O1 $F9', 0, ('$F9', '$F1')),
    # Clean up if we got interupted.
    ('remove test files',
     'rm -f test.mp3 test_mp3.mime test.gif test_gif.mime test.foo',
      0, None),
    ('setup mp3 file', 'cp gslib/test_data/test.mp3 test.mp3', 0, None),
    ('setup mp3 mime', 'echo audio/mpeg >test_mp3.mime', 0, None),
    ('setup gif file', 'cp gslib/test_data/test.gif test.gif', 0, None),
    ('setup gif mime', 'echo image/gif >test_gif.mime', 0, None),
    # TODO: we don't need test.app and test.bin anymore if
    # USE_MAGICFILE=True. Implement a way to test both with and without using
    # magic file.
    #('setup app file', 'echo application/octet-stream >test.app', 0, None),
    ('setup foo file', 'echo foo/bar >test.foo', 0, None),
    ('upload mp3', 'gsutil cp test.mp3 gs://$B1/$O1', 0, None),
    ('verify mp3', 'gsutil ls -L gs://$B1/$O1 | grep MIME | cut -f3 >$F1',
      0, ('$F1', 'test_mp3.mime')),
    ('upload gif', 'gsutil cp test.gif gs://$B1/$O1', 0, None),
    ('verify gif', 'gsutil ls -L gs://$B1/$O1 | grep MIME | cut -f3 >$F1',
      0, ('$F1', 'test_gif.mime')),
    # TODO: The commented-out /noCT test below fails with USE_MAGICFILE=True.
    ('upload mp3/noCT',
      'gsutil -h "Content-Type:" cp test.mp3 gs://$B1/$O1', 0, None),
    ('verify mp3/noCT', 'gsutil ls -L gs://$B1/$O1 | grep MIME | cut -f3 >$F1',
      0, ('$F1', 'test_mp3.mime')),
    ('upload gif/noCT',
      'gsutil -h "Content-Type:" cp test.gif gs://$B1/$O1', 0, None),
    ('verify gif/noCT', 'gsutil ls -L gs://$B1/$O1 | grep MIME | cut -f3 >$F1',
      0, ('$F1', 'test_gif.mime')),
    #('upload foo/noCT', 'gsutil -h "Content-Type:" cp test.foo gs://$B1/$O1',
    #  0, None),
    #('verify foo/noCT', 'gsutil ls -L gs://$B1/$O1 | grep MIME | cut -f3 >$F1',
    #  0, ('$F1', 'test_bin.mime')),
    ('upload mp3/-h gif',
      'gsutil -h "Content-Type:image/gif" cp test.mp3 gs://$B1/$O1', 0, None),
    ('verify mp3/-h gif',
      'gsutil ls -L gs://$B1/$O1 | grep MIME | cut -f3 >$F1',
      0, ('$F1', 'test_gif.mime')),
    ('upload gif/-h gif',
      'gsutil -h "Content-Type:image/gif" cp test.gif gs://$B1/$O1', 0, None),
    ('verify gif/-h gif',
      'gsutil ls -L gs://$B1/$O1 | grep MIME | cut -f3 >$F1',
      0, ('$F1', 'test_gif.mime')),
    ('upload foo/-h gif',
      'gsutil -h "Content-Type: image/gif" cp test.foo gs://$B1/$O1', 0, None),
    ('verify foo/-h gif',
      'gsutil ls -L gs://$B1/$O1 | grep MIME | cut -f3 >$F1',
      0, ('$F1', 'test_gif.mime')),
    ('remove test files',
     'rm -f test.mp3 test_mp3.mime test.gif test_gif.mime test.foo',
      0, None),
  ]

  def _ParseArgs(self):
    self.mv_naming_semantics = False
    self.exclude_symlinks = False
    # self.recursion_requested initialized in command.py (so can be checked
    # in parent class for all commands).
    if self.sub_opts:
      for o, unused_a in self.sub_opts:
        if o == '-e':
          self.exclude_symlinks = True
        if o == '-M':
          # Note that we signal to the cp command to use the alternate naming
          # semantics by passing the undocumented (for internal use) -m option
          # when running the cp command from mv.py. These semantics only apply
          # for mv -R applied to bucket subdirs.
          self.mv_naming_semantics = True
        elif o == '-r' or o == '-R':
          self.recursion_requested = True

  def _SanityCheckRequest(self, src_uri_expansion, exp_dst_uri):
    if src_uri_expansion.IsEmpty():
      raise CommandException('No URIs matched')
    for src_uri in src_uri_expansion.GetSrcUris():
      if src_uri.names_provider():
        raise CommandException('Provider-only src_uri (%s)')
    if src_uri_expansion.IsMultiSrcRequest():
      self._InsistDstUriNamesContainer(exp_dst_uri, True, self.command_name)
      if (exp_dst_uri.is_file_uri()
          and not os.path.exists(exp_dst_uri.object_name)):
        os.makedirs(exp_dst_uri.object_name)

  def _HandleStreamingDownload(self):
    # Destination is <STDOUT>. Manipulate sys.stdout so as to redirect all
    # debug messages to <STDERR>.
    stdout_fp = sys.stdout
    sys.stdout = sys.stderr
    did_some_work = False
    for uri_str in self.args[0:len(self.args)-1]:
      for uri in self.exp_handler.WildcardIterator(uri_str).IterUris():
        if not uri.names_object():
          raise CommandException('Destination Stream requires that '
                                 'source URI %s should represent an object!')
        did_some_work = True
        key = uri.get_key(False, self.headers)
        (elapsed_time, bytes_transferred) = self._PerformDownloadToStream(
            key, uri, stdout_fp, self.headers)
        self.total_elapsed_time += elapsed_time
        self.total_bytes_transferred += bytes_transferred
    if not did_some_work:
      raise CommandException('No URIs matched')
    if self.debug == 3:
      if self.total_bytes_transferred != 0:
        sys.stderr.write(
            'Total bytes copied=%d, total elapsed time=%5.3f secs (%sps)\n' %
                (self.total_bytes_transferred, self.total_elapsed_time,
                 MakeHumanReadable(float(self.total_bytes_transferred) /
                                   float(self.total_elapsed_time))))

  def _SrcDstSame(self, src_uri, dst_uri):
    """Checks if src_uri and dst_uri represent the same object or file.

    We don't handle anything about hard or symbolic links.

    Args:
      src_uri: Source StorageUri.
      dst_uri: Destination StorageUri.

    Returns:
      Bool indicator.
    """
    if src_uri.is_file_uri() and dst_uri.is_file_uri():
      # Translate a/b/./c to a/b/c, so src=dst comparison below works.
      new_src_path = re.sub('%s+\.%s+' % (os.sep, os.sep), os.sep,
                            src_uri.object_name)
      new_src_path = re.sub('^.%s+' % os.sep, '', new_src_path)
      new_dst_path = re.sub('%s+\.%s+' % (os.sep, os.sep), os.sep,
                            dst_uri.object_name)
      new_dst_path = re.sub('^.%s+' % os.sep, '', new_dst_path)
      return (src_uri.clone_replace_name(new_src_path).uri ==
              dst_uri.clone_replace_name(new_dst_path).uri)
    else:
      # TODO: There are cases where copying from src to dst with the same
      # object makes sense, namely, for setting metadata on an object. At some
      # point if we offer a command to do so, add a parameter to the current
      # function to allow this check to be overridden. Note that we want this
      # check to prevent a user from blowing away data using the mv command,
      # with a command like:
      #   gsutil mv -R gs://bucket/abc/* gs://bucket/abc
      return src_uri.uri == dst_uri.uri

  def _ShouldTreatDstUriAsBucketSubDir(self, have_multiple_srcs, dst_uri):
    """
    Checks whether dst_uri should be treated as a bucket "sub-directory". The
    decision about whether something constitutes a bucket "sub-directory"
    depends on whether there are multiple sources in this request. For
    example, when running the command gsutil cp file gs://bucket/abc
    gs://bucket/abc names an object; in contrast, when running the command
    gsutil cp file1 file2 gs://bucket/abc
    gs://bucket/abc names a bucket "sub-directory".

    Note that we don't disallow naming a bucket "sub-directory" where there's
    already an object at that URI. For example it's legitimate (albeit
    confusing) to have an object called gs://bucket/dir and
    then run the command
    gsutil cp file1 file2 gs://bucket/dir
    Doing so will end up with objects gs://bucket/dir, gs://bucket/dir/file1,
    and gs://bucket/dir/file2.

    Args:
      have_multiple_srcs: Bool indicator of whether this is a multi-source
          operation.
      dst_uri: StorageUri to check.

    Returns:
      bool indicator.
    """
    return (self.recursion_requested and have_multiple_srcs
            and dst_uri.names_object())

  def _ShouldTreatDstUriAsSingleton(self, have_multiple_srcs, dst_uri):
    """
    Checks that dst_uri names a singleton (file or object) after
    dir/wildcard expansion. The decision is more nuanced than simply
    dst_uri.names_singleton()) because of the possibility that an object path
    might name a bucket "sub-directory", which in turn depends on whether
    there are multiple sources in the gsutil command being run. For example,
    when running the command:
      gsutil cp file gs://bucket/abc
    gs://bucket/abc names an object; in contrast, when running the command:
      gsutil cp file1 file2 gs://bucket/abc
    gs://bucket/abc names a bucket "sub-directory".

    Args:
      have_multiple_srcs: Bool indicator of whether this is a multi-source
          operation.
      dst_uri: StorageUri to check.

    Returns:
      bool indicator.
    """
    if have_multiple_srcs:
      # Only a file meets the criteria in this case.
      return dst_uri.names_file()
    else:
      return dst_uri.names_singleton()


def _GetPathBeforeFinalDir(uri):
  """
  Returns the part of the path before the final directory component for the
  given URI, handling cases for file system directories, bucket, and bucket
  subdirectories. Example: for gs://bucket/dir/ we'll return 'gs://bucket'.

  Args:
    uri: StorageUri.

  Returns:
    String name of above-described path, sans final path separator.
  """
  sep = uri.delim
  assert not uri.names_file()
  if uri.names_directory():
    return uri.uri.rstrip(sep).rpartition(sep)[0]
  if uri.names_bucket():
    return '%s://' % uri.scheme
  # Else it names a bucket subdir.
  return uri.uri.rstrip(sep).rpartition(sep)[0]
