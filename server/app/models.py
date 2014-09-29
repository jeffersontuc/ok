#pylint: disable=no-member

"""Data models."""

import datetime

from flask import Blueprint
from app import constants

MODEL_BLUEPRINT = Blueprint('models', __name__)

from app import app
from app.needs import Need
from flask import json
from flask.json import JSONEncoder as old_json

from google.appengine.ext import db, ndb

BadValueError = db.BadValueError

# To deal with circular imports
class APIProxy(object):
    def __getattribute__(self, key):
        import app
        return app.api.__getattribute__(key)

APIProxy = APIProxy()

class JSONEncoder(old_json):
    """
    Wrapper class to try calling an object's to_dict() method. This allows
    us to JSONify objects coming from the ORM. Also handles dates & datetimes.
    """
    def default(self, obj): #pylint: disable=E0202
        if isinstance(obj, ndb.Key):
            got = obj.get()
            if not got:
                return None
            return got.to_json()
        elif isinstance(obj, datetime.datetime):
            obj = convert_timezone(obj)
            return obj.strftime(app.config["GAE_DATETIME_FORMAT"])
        if isinstance(obj, ndb.Model):
            return obj.to_json()
        return super(JSONEncoder, self).default(obj)

app.json_encoder = JSONEncoder

def convert_timezone(utc_dt):
    delta = datetime.timedelta(hours = -7)
    return (datetime.datetime.combine(utc_dt.date(),utc_dt.time()) + delta)


class Base(ndb.Model):
    """Shared utilities."""

    @classmethod
    def from_dict(cls, values):
        """Creates an instance from the given values."""
        inst = cls()
        inst.populate(**values) #pylint: disable=star-args
        return inst

    def to_json(self, fields=None):
        """Converts this model to a json dictionary."""
        if fields == True:
            return self.to_dict()
        elif fields == False:
            return {}

        if not fields:
            fields = {}
        if fields:
            result = self.to_dict(include=fields.keys())
        else:
            result = self.to_dict()

        if self.key and (not fields or 'id' in fields):
            result['id'] = self.key.id()

        for key, value in result.items():
            if isinstance(value, ndb.Key):
                value = value.get()
                if value:
                    result[key] = value.to_json(fields.get(key))
                else:
                    result[key] = None
            else:
                try:
                    new_value = app.json_encoder().default(value)
                    result[key] = new_value
                except TypeError:
                    pass
        return result

    @classmethod
    def can(cls, user, need, obj=None, query=None):
        """
        Tells you if the |user| satisfies the given |need| for this object.
        """
        need.set_object(obj or cls)
        return cls._can(user, need, obj, query)

    @classmethod
    def _can(cls, user, need, obj=None, query=None):
        return False


class User(Base):
    """Users."""
    email = ndb.StringProperty(required=True) # Must be associated with some OAuth login.
    login = ndb.StringProperty() # TODO(denero) Legacy of glookup system
    role = ndb.StringProperty(default=constants.STUDENT_ROLE)
    first_name = ndb.StringProperty()
    last_name = ndb.StringProperty()

    def __repr__(self):
        return '<User %r>' % self.email

    @property
    def is_admin(self):
        return self.role == constants.ADMIN_ROLE

    @property
    def logged_in(self):
        return self.email != "_anon"

    @property
    def is_staff(self):
        return self.role == constants.STAFF_ROLE

    @property
    def staffed_courses(self):
        return Course.query(Course.staff == self.key)

    @property
    def courses(self):
        return [group.assignment.get().course for group in self.groups()]

    def groups(self, assignment=None):
        query = Group.query(Group.members == self.key)
        if assignment:
            query = query.filter(Group.assignment == assignment)
        return query

    @classmethod
    def from_dict(cls, values):
        """Creates an instance from the given values."""
        if 'email' not in values:
            raise ValueError("Need to specify an email")
        inst = cls(key=ndb.Key('User', values['email']))
        inst.populate(**values) #pylint: disable=star-args
        return inst

    @classmethod
    def get_or_insert(cls, email, **kwargs):
        assert not isinstance(id, int), "Only string keys allowed for users"
        kwargs['email'] = email
        return super(User, cls).get_or_insert(email, **kwargs)

    @classmethod
    def get_by_id(cls, id, **kwargs):
        assert not isinstance(id, int), "Only string keys allowed for users"
        return super(User, cls).get_by_id(id, **kwargs)

    @property
    def logged_in(self):
        return True

    @classmethod
    def _can(cls, user, need, obj=None, query=None):
        if not user.logged_in:
            return False

        action = need.action
        if action == "get":
            if user.is_admin:
                return True
            if obj:
                if obj.key == user.key:
                    return True

            if user.is_staff:
                for course in user.staffed_courses:
                    if course.key in obj.courses:
                        return True
        elif action == "index":
            if user.is_admin:
                return query

            filters = []
            for course in user.courses:
                if user.key in course.staff:
                    filters.append(User.query().filter(
                        User.courses == course.key))

            filters.append(User.key == user.key)

            if len(filters) > 1:
                return query.filter(ndb.OR(*filters))
            else:
                return query.filter(filters[0])
        elif action in ("create", "put"):
            return user.is_admin
        return False


_AnonUser = None
def AnonymousUser():
    global _AnonUser
    if not _AnonUser:
        _AnonUser = User.get_or_insert("_anon")
    return _AnonUser

class Assignment(Base):
    """
    The Assignment Model
    """
    name = ndb.StringProperty(required=True) # Must be unique to support submission.
    display_name = ndb.StringProperty(required=True) # Name displayed to students
    points = ndb.FloatProperty(required=True)
    creator = ndb.KeyProperty(User, required=True)
    templates = ndb.JsonProperty(required=True)
    course = ndb.KeyProperty('Course', required=True)
    max_group_size = ndb.IntegerProperty(required=True)
    due_date = ndb.DateTimeProperty(required=True)
    active = ndb.ComputedProperty(lambda a: datetime.datetime.now() <= a.due_date)

    @classmethod
    def _can(cls, user, need, obj=None, query=None):
        action = need.action
        if action == "get":
            return True
        elif action == "index":
            return query
        elif action in ("create", "put"):
            return user.is_admin
        return False


class Course(Base):
    """Courses have enrolled students and assignment lists with due dates."""
    institution = ndb.StringProperty(required=True) # E.g., 'UC Berkeley'
    name = ndb.StringProperty(required=True) # E.g., 'CS 61A'
    term = ndb.StringProperty(required=True)
    year = ndb.StringProperty(required=True)
    # TODO: validate offering
    creator = ndb.KeyProperty(User, required=True)
    staff = ndb.KeyProperty(User, repeated=True)
    active = ndb.BooleanProperty(default=True)

    @classmethod
    def _can(cls, user, need, course=None, query=None):
        action = need.action
        if action == "get":
            if user.is_admin:
                return True
            return True
        elif action == "index":
            return query
        elif action in ("create", "delete", "put"):
            return user.is_admin
        elif action == "modify":
            if user.is_admin:
                return True
            if not course:
                raise ValueError("Need instance for get action.")
            return user.key in course.staff
        elif action == "staff":
            if user.is_admin:
                return True
            return user.key in course.staff
        return False

    @property
    def assignments(self):
        return Assignment.query(Assignment.course == self.key)


def validate_messages(_, messages):
    """Messages is a JSON string encoding a map from protocols to data."""
    if not messages:
        raise BadValueError('Empty messages')
    try:
        files = json.loads(messages)
        if not isinstance(files, dict):
            raise BadValueError('messages is not a JSON map')
        for k in files:
            if not isinstance(k, (str, unicode)):
                raise BadValueError('key %r is not a string' % k)
        # TODO(denero) Check that each key corresponds to a known protocol,
        #              and call protocol-specific validators on each value.
    except Exception as exc:
        raise BadValueError(exc)


class Submission(Base):
    """A submission is generated each time a student runs the client."""
    submitter = ndb.KeyProperty(User, required=True)
    assignment = ndb.KeyProperty(Assignment)
    messages = ndb.JsonProperty()
    created = ndb.DateTimeProperty(auto_now_add=True)

    @property
    def group(self):
        submitter = self.submitter.get()
        return submitter.groups(self.assignment.get()).get()

    @classmethod
    def _can(cls, user, need, obj=None, query=None):
        action = need.action
        if action == "get":
            if not obj:
                raise ValueError("Need instance for get action.")
            if user.is_admin or obj.submitter == user.key:
                return True
            if user.is_staff:
                for course in user.staffed_courses:
                    if course.key in obj.submitter.get().courses:
                        return True
            groups = list(user.groups())
            my_group = obj.group

            if groups and my_group and my_group.key in [g.key for g in groups]:
                return True
            return False
        if action in ("create", "put"):
            return user.logged_in

        if action == "index":
            if not user.logged_in:
                return False

            if not query:
                raise ValueError(
                    "Need query instance for Submission index action")

            if user.is_admin:
                return query

            filters = []
            courses = Course.query().filter(Course.staff == user.key)
            for course in courses:
                assignments = Assignment.query().filter(
                    Assignment.course == course).fetch()

                filters.append(Submission.assignment.IN(
                    [assign.key for assign in assignments]))

            for group in user.groups():
                filters.append(Submission.submitter.IN(group.members))
            filters.append(Submission.submitter == user.key)

            if len(filters) > 1:
                return query.filter(ndb.OR(*filters))
            elif filters:
                return query.filter(filters[0])
            else:
                return query
        return False


class SubmissionDiff(Base):
    submission = ndb.KeyProperty(Submission)
    diff = ndb.JsonProperty()

    @property
    def comments(self):
        return Comment.query(ancestor=self.key).order(Comment.created)

    def to_json(self, fields=None):
        dct = super(SubmissionDiff, self).to_json(fields)
        comments = list(self.comments)
        comment_dict = {}
        for comment in comments:
            if comment.filename not in comment_dict:
                comment_dict[comment.filename] = {}
            if comment.line not in comment_dict[comment.filename]:
                comment_dict[comment.filename][comment.line] = []
            comment_dict[comment.filename][comment.line].append(comment)

        dct['comments'] = comment_dict
        return dct

class Comment(Base):
    author = ndb.KeyProperty('User', required=True)
    created = ndb.DateTimeProperty(auto_now_add=True)
    line = ndb.IntegerProperty(required=True)
    message = ndb.TextProperty(required=True)
    draft = ndb.BooleanProperty(required=True, default=True)
    filename = ndb.StringProperty(required=True)

    @classmethod
    def _can(cls, user, need, comment=None, query=None):
        if need.action == "get":
            return user.is_admin or comment.author == user.key
        if need.action == "delete":
            return user.is_admin or comment.author == user.key
        return False


class Version(Base):
    """A version of client-side resources. Used for auto-updating."""
    name = ndb.StringProperty(required=True)
    versions = ndb.StringProperty(repeated=True)
    current_version = ndb.StringProperty()
    base_url = ndb.StringProperty(required=True)

    def to_json(self, fields=None):
        json = super(Version, self).to_json(fields)
        if self.current_version:
            json['current_download_link'] = '/'.join((
                self.base_url, self.current_version, self.name))

        return json

    @classmethod
    def _can(cls, user, need, obj=None, query=None):
        action = need.action

        if action == "delete":
            return False
        if action == "index":
            return query
        return user.is_admin

    @classmethod
    def from_dict(cls, values):
        """Creates an instance from the given values."""
        if 'name' not in values:
            raise ValueError("Need to specify a name")
        inst = cls(key=ndb.Key('Version', values['name']))
        inst.populate(**values) #pylint: disable=star-args
        return inst

    @classmethod
    def get_or_insert(cls, key, **kwargs):
        assert not isinstance(id, int), "Only string keys allowed for versions"
        kwargs['name'] = key
        return super(cls, Version).get_or_insert(key, **kwargs)

    @classmethod
    def get_by_id(cls, key, **kwargs):
        assert not isinstance(id, int), "Only string keys allowed for versions"
        return super(cls, Version).get_by_id(key, **kwargs)

class Group(Base):
    """
    A group is a collection of users who all submit submissions.
    They all can see submissions for an assignment all as a group.
    """
    members = ndb.KeyProperty(kind='User', repeated=True)
    invited_members = ndb.KeyProperty(kind='User', repeated=True)
    assignment = ndb.KeyProperty('Assignment', required=True)

    @classmethod
    def _can(cls, user, need, obj=None, query=None):
        action = need.action
        if not user.logged_in:
            return False

        if action == "index":
            if user.is_admin:
                return query
            return query.filter(Group.members == user.key)

        if user.is_admin:
            return True

        if action == "delete":
            return False
        if action == "invitation":
            return user.key in obj.invited_members
        if action == "member":
            return user.key in obj.members
        if action == "get":
            return user.key in obj.members or user.key in obj.invited_members

        if action in ("create", "put"):
            #TODO(martinis) make sure other students are ok with this group
            if not obj:
                raise ValueError("Need instance for get action.")
            return user.key in obj.members
        return False

    def _pre_put_hook(self):
        max_group_size = self.assignment.get().max_group_size
        if max_group_size and len(self.members) > max_group_size:
            raise BadValueError("Too many members. Max allowed is %s" % (
                max_group_size))

def anon_converter(prop, value):
    if not value.get().logged_in:
        return None

    return value

class AuditLog(Base):
    created = ndb.DateTimeProperty(auto_now_add=True)
    event_type = ndb.StringProperty(required=True)
    user = ndb.KeyProperty('User', required=True, validator=anon_converter)
    description = ndb.StringProperty()
    obj = ndb.KeyProperty()
