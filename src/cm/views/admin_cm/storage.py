# -*- coding: utf-8 -*-
# @COPYRIGHT_begin
#
# Copyright [2010-2014] Institute of Nuclear Physics PAN, Krakow, Poland
#
# Licensed under the Apache License, Version 2.0 (the "License");
#    you may not use this file except in compliance with the License.
#    You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS,
#    WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#    See the License for the specific language governing permissions and
#    limitations under the License.
#
# @COPYRIGHT_end

"""@package src.cm.views.admin_cm.storage

@alldecoratedby{src.cm.utils.decorators.admin_cm_log}

@author Maciej Nabożny <di.dijo@gmail.com>
"""

from cm.utils.exception import CMException
from cm.models.storage import Storage
from cm.models.node import Node
from cm.utils import log
from common.states import storage_states
from cm.utils.decorators import admin_cm_log
import libvirt

# Django templates
from django.template import loader, Context
from django.conf import settings as django_settings
from django.conf import settings


@admin_cm_log(log=True)
def create(caller_id, name, address, directory, capacity):
    """
    Registers new Storage.

    @cmview_admin_cm
    @param_post{name,string} libvirt's pool name
    @param_post{address,string} storage ip address or hostname
    @param_post{directory,string} directory on storage
    @param_post{capacity,int} maximum storage capacity [MB]

    @raises{storage_already_exist,CMException}
    @raises{storage_create,CMException}
    """

    #error if already exists a storage with the same name
    if Storage.objects.filter(name__exact=name).exists():
        raise CMException('storage_already_exist')

    try:
        st = Storage()
        st.name = name
        st.address = address
        st.dir = directory
        st.capacity = capacity
        st.state = storage_states['ok']

    except Exception, e:
        log.debug(caller_id, 'Cannot register storage - missing element: %s' % str(e))
        raise CMException('storage_create')

    try:
        st.save()
    except Exception, e:
        log.debug(caller_id, 'Cannot save storage in database: %s' % str(e))
        raise CMException('storage_create')


@admin_cm_log(log=True)
def get_list(caller_id):
    """
    Returns list of Storages.

    @cmview_admin_cm
    @response{list(dict)} Storage.dict property for each Storage
    """
    return [st.dict for st in Storage.objects.all()]


@admin_cm_log(log=True)
def lock(caller_id, storage_id):
    """
    Locks specified Storage.

    @cmview_admin_cm
    @param_post{storage_id,int}
    """
    try:
        st = Storage.objects.get(pk=storage_id)
        st.state = storage_states['locked']
        st.save()
    except Exception, e:
        log.debug(caller_id, 'Cannot lock storage: %s' % str(e))
        raise CMException('storage_lock')


@admin_cm_log(log=True)
def unlock(caller_id, storage_id):
    """
    Unlocks specified Storage.

    @cmview_admin_cm
    @param_post{storage_id,int}
    """
    try:
        st = Storage.objects.get(pk=storage_id)
        st.state = storage_states['ok']
        st.save()
    except Exception, e:
        log.debug(caller_id, 'Cannot unlock storage: %s' % str(e))
        raise CMException('storage_unlock')


@admin_cm_log(log=True)
def mount(caller_id, storage_id=None, node_id=None):
    """
    Mounts specified Storages on specified Node.

    @cmview_admin_cm
    @param_post{storage_id} id of Storage to mount (None for all)
    @param_post{node_id} id of Node where to mount Storage (None for all)

    @response{dict} Node's responses
    """
    #if node_id is sent, get that node, otherwise every node
    if node_id:
        nodes = Node.objects.filter(id__exact=node_id)
    else:
        nodes = Node.objects.all()

    #if storage_id is sent, get that storage, otherwise every storage
    if storage_id:
        storages = Storage.objects.filter(id__exact=storage_id)
    else:
        storages = Storage.objects.all()

    node_response = {}
    for node in nodes:
        log.debug(caller_id, "Mounting node: %d" % node.id)
        storage_response = {}
        try:
            conn = libvirt.open(node.conn_string)
        except Exception, e:
            log.debug(caller_id, 'Cannot connect to libvirt: %s' % str(e))
            node.lock()
            node.save()
            raise CMException('storage_libvirt')

        for storage in storages:
            try:
                st_template = loader.get_template("storage_%s.xml" % storage.transport)
                log.info(caller_id, "Rendered template: %s" % st_template)
            except Exception, e:
                raise CMException('cm_storage_mount')

            try:
                # Create pool from xml template
                #TODO: change 331 to be read from config
                context = Context({'storage': storage, 'cc_userid': settings.CC_USERID, 'cc_groupid': settings.CC_GROUPID})
                t = st_template.render(context)
                log.info(caller_id, t)
                # Define pool, then set autostart, create mountpoint and start it
                try:
                    pool = conn.storagePoolDefineXML(t, 0)
                except Exception, e:
                    log.debug(caller_id, "Cannot define storage: %s" % str(e))
                    pool = conn.storagePoolLookupByName(storage.name)
                pool.setAutostart(1)
                pool.build(0)
                pool.create(0)
                storage_response[str(storage.id)] = 'ok'
            except Exception, e:
                log.debug(caller_id, 'Cannot mount storage %d on node %d: %s' % (storage_id, node_id, str(e)))
                storage_response[str(storage.id)] = 'failed'
        node_response[str(node.id)] = storage_response

    return node_response


@admin_cm_log(log=True)
def check(caller_id, node_list):
    """
    Check each Node for mounted and unmounted Storages.

    @cmview_admin_cm
    @param_post{node_list,list(int)} list of Node ids

    @response{list(dict)} for each node dict with fields:
    @dictkey{mounted,list} list of Storages mounted to each Node
    @dictkey{unmounted,list} list of Storages not mounted to each Node
    """

    storage_names = Storage.objects.values_list('name', flat=True)

    result = {}
    for n in node_list:
        # Connect and get running storage list
        node = Node.objects.get(pk=n)
        conn = libvirt.open(node.conn_string)
        pools = conn.listStoragePools()

        mounted = []
        for pool in pools:
            if pool in storage_names:
                mounted.append(storage_names[storage_names.index(pool)])

        # Other storages are unmounted
        unmounted = storage_names
        for pool in mounted:
            try:
                unmounted.remove(pool)
            except:
                pass
        result['%d' % n] = {'mounted': mounted, 'unmounted': unmounted}
    return result
