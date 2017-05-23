# -*- coding: utf-8 -*-
"""
ModularServer
=============

A visualization server which renders a model via one or more elements.

The concept for the modular visualization server as follows:
A visualization is composed of VisualizationElements, each of which defines how
to generate some visualization from a model instance and render it on the
client. VisualizationElements may be anything from a simple text display to
a multilayered HTML5 canvas.

The actual server is launched with one or more VisualizationElements;
it runs the model object through each of them, generating data to be sent to
the client. The client page is also generated based on the JavaScript code
provided by each element.

This file consists of the following classes:

VisualizationElement: Parent class for all other visualization elements, with
                      the minimal necessary options.
PageHandler: The handler for the visualization page, generated from a template
             and built from the various visualization elements.
SocketHandler: Handles the websocket connection between the client page and
                the server.
ModularServer: The overall visualization application class which stores and
               controls the model and visualization instance.


ModularServer should *not* need to be subclassed on a model-by-model basis; it
should be primarily a pass-through for VisualizationElement subclasses, which
define the actual visualization specifics.

For example, suppose we have created two visualization elements for our model,
called canvasvis and graphvis; we would launch a server with:

    server = ModularServer(MyModel, [canvasvis, graphvis], name="My Model")
    server.launch()

The client keeps track of what step it is showing. Clicking the Step button in
the browser sends a message requesting the viz_state corresponding to the next
step position, which is then sent back to the client via the websocket.

The websocket protocol is as follows:
Each message is a JSON object, with a "type" property which defines the rest of
the structure.

Server -> Client:
    Send over the model state to visualize.
    Model state is a list, with each element corresponding to a div; each div
    is expected to have a render function associated with it, which knows how
    to render that particular data. The example below includes two elements:
    the first is data for a CanvasGrid, the second for a raw text display.

    {
    "type": "viz_state",
    "data": [{0:[ {"Shape": "circle", "x": 0, "y": 0, "r": 0.5,
                "Color": "#AAAAAA", "Filled": "true", "Layer": 0,
                "text": 'A', "text_color": "white" }]},
            "Shape Count: 1"]
    }

    Informs the client that the model is over.
    {"type": "end"}
    
    Informs the client of the current model's parameters
    {
    "type": "model_params",
    "params": 'dict' of model params, (i.e. {arg_1: val_1, ...})
    }

Client -> Server:
    Reset the model.
    TODO: Allow this to come with parameters
    {
    "type": "reset"
    }

    Get a given state.
    {
    "type": "get_step",
    "step:" index of the step to get.
    }
    
    Submit model parameter updates
    {
    "type": "submit_params",
    "param": name of model parameter
    "value": new value for 'param'
    }
    
    Get the model's parameters
    {
    "type": "get_params"
    }

"""
import os
import inspect
from collections import OrderedDict

import tornado.autoreload
import tornado.ioloop
import tornado.web
import tornado.websocket
import tornado.escape
import tornado.gen

import webbrowser

# Suppress several pylint warnings for this file.
# Attributes being defined outside of init is a Tornado feature.
# pylint: disable=attribute-defined-outside-init


class VisualizationElement:
    """
    Defines an element of the visualization.

    Attributes:
        package_includes: A list of external JavaScript files to include that
                          are part of the Mesa packages.
        local_includes: A list of JavaScript files that are local to the
                        directory that the server is being run in.
        js_code: A JavaScript code string to instantiate the element.

    Methods:
        render: Takes a model object, and produces JSON data which can be sent
                to the client.

    """

    package_includes = []
    local_includes = []
    js_code = ''
    render_args = {}

    def __init__(self):
        pass

    def render(self, model):
        """ Build visualization data from a model object.

        Args:
            model: A model object

        Returns:
            A JSON-ready object.

        """
        return "<b>VisualizationElement goes here</b>."

# =============================================================================
# Actual Tornado code starts here:


class PageHandler(tornado.web.RequestHandler):
    """ Handler for the HTML template which holds the visualization. """

    def get(self):
        elements = self.application.visualization_elements
        for i, element in enumerate(elements):
            element.index = i
        self.render("modular_template.html", port=self.application.port,
                    model_name=self.application.model_name,
                    package_includes=self.application.package_includes,
                    local_includes=self.application.local_includes,
                    scripts=self.application.js_code)


class SocketHandler(tornado.websocket.WebSocketHandler):
    """ Handler for websocket. """
    def open(self):
        if self.application.verbose:
            print("Socket opened!")

    def check_origin(self, origin):
        return True

    @property
    def viz_state_message(self):
        return {
            "type": "viz_state",
            "data": self.application.render_model()
        }

    def on_message(self, message):
        """ Receiving a message from the websocket, parse, and act accordingly.

        """
        if self.application.verbose:
            print(message)
        msg = tornado.escape.json_decode(message)

        if msg["type"] == "get_step":
            if not self.application.model.running:
                self.write_message({"type": "end"})
            else:
                self.application.model.step()
                self.write_message(self.viz_state_message)

        elif msg["type"] == "reset":
            self.application.reset_model()
            self.write_message(self.viz_state_message)

        elif msg["type"] == "submit_params":
            param = msg["param"]
            value = msg["value"]

            # Is the param editable?
            if param in self.application.user_params:

                # Handle model args
                if param in self.application.model_args:
                    self.application.model_args[param] = value

                # Handle model kwargs
                elif param in self.application.model_kwargs:
                    self.application.model_kwargs[param] = value

        elif msg["type"] == "get_params":
            self.write_message({
                "type": "model_params",
                "params": self.application.user_params
            })

        else:
            if self.application.verbose:
                print("Unexpected message!")


class ModularServer(tornado.web.Application):
    """ Main visualization application. """
    verbose = True

    model_name = "Mesa Model"
    model_cls = None  # A model class
    portrayal_method = None
    port = 8521  # Default port to listen on
    canvas_width = 500
    canvas_height = 500
    grid_height = 0
    grid_width = 0

    max_steps = 100000

    model_args = ()
    model_kwargs = {}

    # Handlers and other globals:
    page_handler = (r'/', PageHandler)
    socket_handler = (r'/ws', SocketHandler)
    static_handler = (r'/static/(.*)', tornado.web.StaticFileHandler,
                      {"path": os.path.dirname(__file__) + "/templates"})
    local_handler = (r'/local/(.*)', tornado.web.StaticFileHandler,
                     {"path": ''})

    handlers = [page_handler, socket_handler, static_handler, local_handler]

    settings = {"debug": True,
                "autoreload": False,
                "template_path": os.path.dirname(__file__) + "/templates"}

    EXCLUDE_LIST = ('width', 'height',)

    def __init__(self, model_cls, visualization_elements, name="Mesa Model",
                 *args, **kwargs):
        """ Create a new visualization server with the given elements. """
        # Prep visualization elements:
        self.visualization_elements = visualization_elements
        self.package_includes = set()
        self.local_includes = set()
        self.js_code = []
        for element in self.visualization_elements:
            for include_file in element.package_includes:
                self.package_includes.add(include_file)
            for include_file in element.local_includes:
                self.local_includes.add(include_file)
            self.js_code.append(element.js_code)

        # Initializing the model
        self.model_name = name
        self.model_cls = model_cls

        # Determine current parameter exclude list
        if 'exclude_list' in kwargs:
            self.exclude_list = kwargs.pop('exclude_list')
        else:
            self.exclude_list = self.EXCLUDE_LIST

        # Infer args and kwargs from the model cls' constructor, use args/kwargs provide to override the default
        # model's default args/kwargs, and then capture the resulting model args/kwargs to be used when resetting model
        sig = inspect.signature(model_cls.__init__)

        # Determine non-keyword arguments for the model
        _args = OrderedDict()
        for a in sig.parameters:
            if sig.parameters[a].default is inspect._empty and a != 'self':
                if a in self.exclude_list:
                    _args[a] = args[list(sig.parameters).index(a)-1]
                else:
                    _args[a] = None

        for i, a in enumerate(sig.parameters):

            # Determine which arguments not to allow to be editable?
            if a in ['self']:
                continue

            # Handle model args
            elif a in _args and i <= len(args):
                _args[a] = args[i-1]    # always skip 'self' param in args

            # Handle model kwargs
            elif a not in kwargs and a not in _args and sig.parameters[a].default is not inspect._empty:
                kwargs[a] = sig.parameters[a].default

        self.model_args = _args
        self.model_kwargs = kwargs
        self.reset_model()

        # Initializing the application itself:
        super().__init__(self.handlers, **self.settings)

    @property
    def user_params(self):
        return {**{a: self.model_args[a] for a in self.model_args if a not in self.exclude_list},
                  **self.model_kwargs}

    def reset_model(self):
        """ Reinstantiate the model object, using the current parameters. """
        self.model = self.model_cls(*(tuple(self.model_args.values())), **self.model_kwargs)

    def render_model(self):
        """ Turn the current state of the model into a dictionary of
        visualizations

        """
        visualization_state = []
        for element in self.visualization_elements:
            element_state = element.render(self.model)
            visualization_state.append(element_state)
        return visualization_state

    def launch(self, port=None):
        """ Run the app. """
        startLoop = not tornado.ioloop.IOLoop.initialized()
        if port is not None:
            self.port = port
        url = 'http://127.0.0.1:{PORT}'.format(PORT=self.port)
        print('Interface starting at {url}'.format(url=url))
        self.listen(self.port)
        webbrowser.open(url)
        tornado.autoreload.start()
        if startLoop:
            tornado.ioloop.IOLoop.instance().start()
