"""
Facilities to interface with the Heliophysics Events Knowledgebase.
"""
import json
import codecs
import urllib
import inspect
from itertools import chain

import astropy.table
from astropy.table import Row
from astropy.time import Time

import sunpy.net._attrs as core_attrs
from sunpy.net import attr
from sunpy.net.base_client import BaseClient, QueryResponseTable
from sunpy.net.hek import attrs
from sunpy.util import dict_keys_same, unique
from sunpy.util.xml import xml_to_dict

__all__ = ['HEKClient', 'HEKTable', 'HEKRow']

DEFAULT_URL = 'https://www.lmsal.com/hek/her?'


def _freeze(obj):
    """ Create hashable representation of result dict. """
    if isinstance(obj, dict):
        return tuple((k, _freeze(v)) for k, v in obj.items())
    if isinstance(obj, list):
        return tuple(_freeze(elem) for elem in obj)
    return obj


class HEKClient(BaseClient):
    """
    Provides access to the Heliophysics Event Knowledgebase (HEK).

    The HEK stores solar feature and event data generated by algorithms and
    human observers.
    """
    # FIXME: Expose fields in .attrs with the right types
    # that is, not all StringParamWrapper!

    default = {
        'cosec': '2',
        'cmd': 'search',
        'type': 'column',
        'event_type': '**',
    }
    # Default to full disk.
    attrs.walker.apply(attrs.SpatialRegion(), {}, default)

    def __init__(self, url=DEFAULT_URL):
        self.url = url

    def _download(self, data):
        """ Download all data, even if paginated. """
        page = 1
        results = []

        while True:
            data['page'] = page
            fd = urllib.request.urlopen(self.url+urllib.parse.urlencode(data))
            try:
                result = codecs.decode(fd.read(), encoding='utf-8', errors='replace')
                result = json.loads(result)
            except Exception as e:
                raise IOError("Failed to load return from the HEKClient.") from e
            finally:
                fd.close()
            results.extend(result['result'])

            if not result['overmax']:
                if len(results) > 0:
                    return astropy.table.Table(dict_keys_same(results))
                else:
                    return astropy.table.Table()

            page += 1

    def search(self, *args, **kwargs):
        """
        Retrieves information about HEK records matching the criteria
        given in the query expression. If multiple arguments are passed,
        they are connected with AND. The result of a query is a list of
        unique HEK Response objects that fulfill the criteria.

        Examples
        -------
        >>> from sunpy.net import attrs as a, Fido
        >>> timerange = a.Time('2011/08/09 07:23:56', '2011/08/09 12:40:29')
        >>> res = Fido.search(timerange, a.hek.FL, a.hek.FRM.Name == "SWPC")  # doctest: +REMOTE_DATA
        >>> res  #doctest: +SKIP
        <sunpy.net.fido_factory.UnifiedResponse object at ...>
        Results from 1 Provider:
        <BLANKLINE>
        2 Results from the HEKClient:
                 SOL_standard          active ... skel_startc2 sum_overlap_scores
        ------------------------------ ------ ... ------------ ------------------
        SOL2011-08-09T07:19:00L227C090   true ...         None                  0
        SOL2011-08-09T07:48:00L296C073   true ...         None                  0
        <BLANKLINE>
        <BLANKLINE>
        """
        query = attr.and_(*args)

        data = attrs.walker.create(query, {})
        ndata = []
        for elem in data:
            new = self.default.copy()
            new.update(elem)
            ndata.append(new)

        if len(ndata) == 1:
            return HEKTable(self._download(ndata[0]), client=self)
        else:
            return HEKTable(self._merge(self._download(data) for data in ndata), client=self)

    def _merge(self, responses):
        """ Merge responses, removing duplicates. """
        return list(unique(chain.from_iterable(responses), _freeze))

    def fetch(self, *args, **kwargs):
        """
        This is a no operation function as this client does not download data.
        """
        return NotImplemented

    @classmethod
    def _attrs_module(cls):
        return 'hek', 'sunpy.net.hek.attrs'

    @classmethod
    def _can_handle_query(cls, *query):
        required = {core_attrs.Time}
        optional = {i[1] for i in inspect.getmembers(attrs, inspect.isclass)} - required
        qr = tuple(x for x in query if not isinstance(x, attrs.EventType))
        return cls.check_attr_types_in_query(qr, required, optional)


class HEKRow(Row):
    """
    Handles the response from the HEK.  Each HEKRow object is a subclass
    of `astropy.Table.row`.  The column-row key-value pairs correspond to the
    HEK feature/event properties and their values, for that record from the
    HEK.  Each HEKRow object also has extra properties that relate HEK
    concepts to VSO concepts.
    """
    @property
    def vso_time(self):
        return core_attrs.Time(
            Time.strptime(self['event_starttime'], "%Y-%m-%dT%H:%M:%S"),
            Time.strptime(self['event_endtime'], "%Y-%m-%dT%H:%M:%S")
        )

    @property
    def vso_instrument(self):
        if self['obs_instrument'] == 'HEK':
            raise ValueError("No instrument contained.")
        return core_attrs.Instrument(self['obs_instrument'])

    @property
    def vso_all(self):
        return attr.and_(self.vso_time, self.vso_instrument)

    def get_voevent(self, as_dict=True,
                    base_url="http://www.lmsal.com/hek/her?"):
        """Retrieves the VOEvent object associated with a given event and
        returns it as either a Python dictionary or an XML string."""

        # Build URL
        params = {
            "cmd": "export-voevent",
            "cosec": 1,
            "ivorn": self['kb_archivid']
        }
        url = base_url + urllib.parse.urlencode(params)

        # Query and read response
        response = urllib.request.urlopen(url).read()

        # Return a string or dict
        if as_dict:
            return xml_to_dict(response)
        else:
            return response

    def get(self, key, default=None):
        try:
            return self[key]
        except KeyError:
            return default


class HEKTable(QueryResponseTable):
    """
    A container for data returned from HEK searches.
    """
    Row = HEKRow
