#!/usr/bin/env python
import json
import optparse
import urllib
import os.path
import os
from operator import itemgetter
import tarfile
import zipfile

__version__ = "1.0.0"
CHUNK_SIZE = 2**20 #1mb
VALID_CHARS = '.-()[]0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ '


def splitext(path):
    # extract the folder path and extension of a file from its path
    for ext in ['.tar.gz', '.tar.bz2']:
        if path.endswith(ext):
            path, ext = path[:-len(ext)], path[-len(ext):]
            break
    else:
        path, ext = os.path.splitext(path)
    return path, ext[1:]


def chunk_write( source_stream, target_stream, source_method = "read", target_method="write" ):
    source_method = getattr( source_stream, source_method )
    target_method = getattr( target_stream, target_method )
    while True:
        chunk = source_method( CHUNK_SIZE )
        if chunk:
            target_method( chunk )
        else:
            break


def deconstruct_multi_filename( multi_filename ):
    keys = [ 'primary', 'id', 'name', 'visible', 'file_type' ]
    return ( dict( zip( keys, multi_filename.split('_') ) ) )


def construct_multi_filename( id, name, file_type ):
    """ Implementation of *Number of Output datasets cannot be determined until tool run* from documentation_.
    .. _documentation: http://wiki.galaxyproject.org/Admin/Tools/Multiple%20Output%20Files
    """
    filename = "%s_%s_%s_%s_%s" % ( 'primary', id, name, 'visible', file_type )
    return filename


def download_from_query( query_data, target_output_filename  ):
    """ Download file from the json data and write it to target_output_filename.
    """
    query_url = query_data.get( 'url' )
    query_file_type = query_data.get( 'extension' )
    query_stream = urllib.urlopen( query_url )
    output_stream = open( target_output_filename, 'wb' )
    chunk_write( query_stream, output_stream )
    query_stream.close()
    output_stream.close()

def store_file_from_tarfile( file_object, target_output_filename, isString=False ):
    # store the file_object (from tarfile) on the filesystem
    output_stream = open( target_output_filename, 'wb' )
    output_stream.write(file_object.read())
    output_stream.close()


def download_extra_data( query_ext_data, base_path ):
    """ Download any extra data defined in the JSON.
    NOTE: the "path" value is a relative path to the file on our
    file system. This is slightly dangerous and we should make every effort
    to avoid a malicious absolute path to write the file elsewhere on the
    filesystem.
    """
    for ext_data in query_ext_data:
        if not os.path.exists( base_path ):
            os.mkdir( base_path )
        query_stream = urllib.urlopen( ext_data.get( 'url' ) )
        ext_path = ext_data.get( 'path' )
        os.makedirs( os.path.normpath( '/'.join( [ base_path, os.path.dirname( ext_path ) ] ) ) )
        output_stream = open( os.path.normpath( '/'.join( [ base_path, ext_path ] ) ), 'wb' )
        chunk_write( query_stream, output_stream )
        query_stream.close()
        output_stream.close()


def metadata_to_json( dataset_id, metadata, filename, ds_type='dataset', primary=False):
    """ Return line separated JSON """
    meta_dict = dict( type = ds_type,
                      ext = metadata.get( 'extension' ),
                      filename = filename,
                      name = metadata.get( 'name' ),
                      metadata = metadata.get( 'metadata', {} ) )
    if metadata.get( 'extra_data', None ):
        meta_dict[ 'extra_files' ] = '_'.join( [ filename, 'files' ] )
    if primary:
        meta_dict[ 'base_dataset_id' ] = dataset_id
    else:
        meta_dict[ 'dataset_id' ] = dataset_id
    return "%s\n" % json.dumps( meta_dict )


def walk_on_archive(target_output_filename, check_ext, archive_library, archive_name, appdata_path, db_key="?"):
    # fix archive name using valid chars only
    archive_name = ''.join(e for e in archive_name if e in VALID_CHARS)
    archive_name = archive_name.replace("_", "-").replace(".", "-")
    if archive_library is "zipfile":
        # iterate over entries inside the archive [zip]
        with zipfile.ZipFile( target_output_filename, check_ext ) as zf:
            for entry in zf.namelist():
                # if entry is file
                if entry.startswith("%s/" % entry.rstrip("/")) is False:
                    # retrieve file name
                    # the underscore character is reserved
                    filename = os.path.basename( entry.split("/")[-1] ).replace("_", "-")
                    # retrieve file extension
                    extension = splitext( filename )[1]
                    # if no extension use 'auto'
                    if (len(extension) == 0):
                        extension = "auto"
                    # pattern: (?P<identifier_0>[^_]+)_(?P<identifier_1>[^_]+)_(?P<ext>[^_]+)_(?P<dbkey>[^_]+)
                    filename_with_collection_prefix = archive_name + "_" + filename + "_" + extension + "_" + db_key
                    # store current entry on filesystem
                    zf.extract( filename_with_collection_prefix, appdata_path )
    elif archive_library is "tarfile":
        # iterate over entries inside the archive [gz, bz2, tar]
        with tarfile.open( target_output_filename, check_ext ) as tf:
            for entry in tf:
                if entry.isfile():
                    fileobj = tf.extractfile( entry )
                    # retrieve file name
                    # the underscore character is reserved
                    filename = os.path.basename( (entry.name).split("/")[-1] ).replace("_", "-")
                    # retrieve file extension
                    extension = splitext( filename )[1]
                    # if no extension use 'auto'
                    if (len(extension) == 0):
                        extension = "auto"
                    # pattern: (?P<identifier_0>[^_]+)_(?P<identifier_1>[^_]+)_(?P<ext>[^_]+)_(?P<dbkey>[^_]+)
                    filename_with_collection_prefix = archive_name + "_" + filename + "_" + extension + "_" + db_key
                    target_entry_output_filename = os.path.join(appdata_path, filename_with_collection_prefix)
                    # store current entry on filesystem
                    store_file_from_tarfile( fileobj, target_entry_output_filename )
    return True


def download_files_and_write_metadata(query_item, json_params, output_base_path, metadata_parameter_file, primary, appdata_path, options, args):
    """ Main work function that operates on the JSON representation of
    one dataset and its metadata. Returns True.
    """
    dataset_url, output_filename, \
        extra_files_path, file_name, \
        ext, out_data_name, \
        hda_id, dataset_id = set_up_config_values(json_params)
    extension = query_item.get( 'extension' )
    url = query_item.get( 'url' )
    filename = query_item.get( 'name' )

    # the organize parameter is considered for archives only
    organize = query_item.get( 'organize', None )
    if organize is None:
        organize = False
    else:
        if organize.lower() == "true":
            organize = True
        elif organize.lower() == "false":
            organize = False
        else:
            # if organize parameter is malformed -> set organize to False
            organize = False

    # check file extension
    # if the file is an archive -> do not write metadata and extract files
    check_ext = ""
    archive_library = None
    if ( url.endswith( "gz" ) ):
        check_ext = "r:gz"
        archive_library = "tarfile"
    elif ( url.endswith( "bz2" ) ):
        check_ext = "r:bz2"
        archive_library = "tarfile"
    elif ( url.endswith( "tar" ) ):
        check_ext = "r:"
        archive_library = "tarfile"
    elif ( url.endswith( "zip" ) ):
        check_ext = "r"
        archive_library = "zipfile"
    isArchive = bool( check_ext and check_ext.strip() )

    extra_data = query_item.get( 'extra_data', None )
    if primary:
        filename = ''.join( c in VALID_CHARS and c or '-' for c in filename )
        name = construct_multi_filename( hda_id, filename, extension )
        target_output_filename = os.path.normpath( '/'.join( [ output_base_path, name ] ) )
        if (isArchive is False) or ((isArchive is True) and (organize is False)):
            metadata_parameter_file.write( metadata_to_json( dataset_id, query_item,
                                                             target_output_filename,
                                                             ds_type='new_primary_dataset',
                                                             primary=primary) )
    else:
        target_output_filename = output_filename
        if (isArchive is False) or ((isArchive is True) and (organize is False)):
            metadata_parameter_file.write( metadata_to_json( dataset_id, query_item,
                                                             target_output_filename,
                                                             ds_type='dataset',
                                                             primary=primary) )
    
    if (isArchive is False) or ((isArchive is True) and (organize is False)):
        download_from_query( query_item, target_output_filename )
    else:
        # if the current entry is an archive download it inside appdata folder
        target_output_path = os.path.join(appdata_path, filename)
        download_from_query( query_item, target_output_path )
    if extra_data:
        # just download extra data
        extra_files_path = ''.join( [ target_output_filename, 'files' ] )
        download_extra_data( extra_data, extra_files_path )

    # if the current file is an archive and want to organize the content 
    # -> decompress the archive and populate the collection (has to be defined in the tool xml schema)
    if isArchive and organize:
        # set the same db_key for each file inside the archive
        # use the db_key associated to the archive (if it exists)
        db_key = "?"
        archive_metadata = query_item.get( 'metadata', None )
        if archive_metadata is not None:
            try:
                db_key = archive_metadata.get( 'db_key' )
            except:
                pass
        archive_name = query_item.get( 'name', None )
        if archive_name is None:
            archive_name = filename
        # iterate over the archive content
        walk_on_archive(target_output_path, check_ext, archive_library, archive_name, appdata_path, db_key)

    return True


def set_up_config_values(json_params):
    """ Parse json_params file and return a tuple of necessary configuration
    values.
    """
    datasource_params = json_params.get( 'param_dict' )
    dataset_url = datasource_params.get( 'URL' )
    output_filename = datasource_params.get( 'output1', None )
    output_data = json_params.get( 'output_data' )
    extra_files_path, file_name, ext, out_data_name, hda_id, dataset_id = \
      itemgetter('extra_files_path', 'file_name', 'ext', 'out_data_name', 'hda_id', 'dataset_id')(output_data[0])
    return (dataset_url, output_filename,
            extra_files_path, file_name,
            ext, out_data_name,
            hda_id, dataset_id)


def download_from_json_data( options, args ):
    """ Parse the returned JSON data and download files. Write metadata
    to flat JSON file.
    """
    output_base_path = options.path
    appdata_path = options.appdata
    if not os.path.exists(appdata_path):
        os.makedirs(appdata_path)

    # read tool job configuration file and parse parameters we need
    json_params = json.loads( open( options.json_param_file, 'r' ).read() )
    #print("json_params: "+str(json_params))
    
    dataset_url, output_filename, \
        extra_files_path, file_name, \
        ext, out_data_name, \
        hda_id, dataset_id = set_up_config_values(json_params)
    # line separated JSON file to contain all dataset metadata
    metadata_parameter_file = open( json_params['job_config']['TOOL_PROVIDED_JOB_METADATA_FILE'], 'wb' )

    # get JSON response from data source
    # TODO: make sure response is not enormous
    query_params = json.loads(urllib.urlopen( dataset_url ).read())
    # download and write files
    primary = False
    #primary = True
    # query_item, hda_id, output_base_path, dataset_id
    for query_item in query_params:
        if isinstance( query_item, list ):
            # TODO: do something with the nested list as a collection
            for query_subitem in query_item:
                primary = download_files_and_write_metadata(query_subitem, json_params, output_base_path,
                                                            metadata_parameter_file, primary, appdata_path, options, args)

        elif isinstance( query_item, dict ):
            primary = download_files_and_write_metadata(query_item, json_params, output_base_path,
                                                        metadata_parameter_file, primary, appdata_path, options, args)
    metadata_parameter_file.close()

def __main__():
    """ Read the JSON return from a data source. Parse each line and request
    the data, download to "newfilepath", and write metadata.

    Schema
    ------

        [ {"url":"http://url_of_file",
           "name":"My Archive",
           "extension":"tar.gz",
           "organize":"true",
           "metadata":{"db_key":"hg38"},
           "extra_data":[ {"url":"http://url_of_ext_file",
                           "path":"rel/path/to/ext_file"}
                        ]
          }
        ]

    """
    # Parse the command line options
    usage = "Usage: json_collect_data_source.py max_size --json_param_file filename [options]"
    parser = optparse.OptionParser(usage = usage)
    parser.add_option("-j", "--json_param_file", type="string",
                    action="store", dest="json_param_file", help="json schema return data")
    parser.add_option("-p", "--path", type="string",
                    action="store", dest="path", help="new file path")
    # set appdata: temporary directory in which the archives will be decompressed
    parser.add_option("-a", "--appdata", type="string",
                    action="store", dest="appdata", help="appdata folder name")
    parser.add_option("-v", "--version", action="store_true", dest="version",
                    default=False, help="display version and exit")

    (options, args) = parser.parse_args()
    if options.version:
        print __version__
    else:
        download_from_json_data( options, args )


if __name__ == "__main__": __main__()
