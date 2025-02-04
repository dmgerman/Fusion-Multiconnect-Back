import adsk.core
import os
import adsk.fusion

from ...lib import fusionAddInUtils as futil
from ... import config
import math
import collections
import traceback


UserParm = collections.namedtuple('UserParm', 'name value unit desc')
FParm = collections.namedtuple('FParm', 'value fRef')
SketchAxes = collections.namedtuple('SketchAxes', 'x y')

valueFromExpr = adsk.core.ValueInput.createByString


app = adsk.core.Application.get()
ui = app.userInterface




design = adsk.fusion.Design.cast(app.activeProduct)
root = design.rootComponent
features = root.features

# We will create and use some user parameters
userParams = design.userParameters


# Create a new user parameter if it doesn't exist
paramName = "DotRadius"
paramValue = 1.015  # Default value
paramUnit = "cm"  # Supports 'mm', 'cm', 'in', etc.

existingParam = userParams.itemByName(paramName)
if existingParam is None:
    userParams.add(paramName, adsk.core.ValueInput.createByReal(paramValue), paramUnit, "Radius of the connector dot")

dotDiameter = userParams.itemByName(paramName)

# TODO move these into the command dialog
onRampEveryXSlots = 1
distanceBetweenSlots = 2.5
baseThickness = 0.3
totalHeight = 2.5
computeCut = False

# TODO *** Specify the command identity information. ***
CMD_ID = f'{config.COMPANY_NAME}_{config.ADDIN_NAME}_cmdDialog'
CMD_NAME = 'Multiconnect Back Genrator'
CMD_Description = 'A Fusion Add-in to create Multiconnect compatible backs'

# Specify that the command will be promoted to the panel.
IS_PROMOTED = True

# TODO *** Define the location where the command button will be created. ***
# This is done by specifying the workspace, the tab, and the panel, and the 
# command it will be inserted beside. Not providing the command to position it
# will insert it at the end.
WORKSPACE_ID = 'FusionSolidEnvironment'
PANEL_ID = 'SolidScriptsAddinsPanel'
COMMAND_BESIDE_ID = 'ScriptsManagerCommand'

# Resource location for command icons, here we assume a sub folder in this directory named "resources".
ICON_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'resources', '')

# Local list of event handlers used to maintain a reference so
# they are not released and garbage collected.
local_handlers = []

onRampEveryXSlotsParm = "onRampEveryXSlots"
distanceBetweenSlotsParm = "distanceBetweenSlots"
baseThicknessParm = "baseThickness"
totalHeightParm = "totalHeight"
computeCutParm = "computeCut"

generalModelUserParms = [
    UserParm(onRampEveryXSlotsParm, onRampEveryXSlots, "", 'not sure what it does'),
    UserParm(distanceBetweenSlotsParm, distanceBetweenSlots, "cm", 'distance between slots'),
    UserParm(baseThicknessParm, baseThickness, "cm", 'base thickness'),
    UserParm(totalHeightParm, totalHeight, "cm", 'total height'),
    UserParm(computeCutParm, 1 if computeCut else 0,  '', 'meaning'),
]


# we need to define this here so it becomes global
dUserParms = None

# the current sketch axes
sketchAxes = None


# Executed when add-in is run.
def start():
    # Create a command Definition.
    cmd_def = ui.commandDefinitions.addButtonDefinition(CMD_ID, CMD_NAME, CMD_Description, ICON_FOLDER)

    # Define an event handler for the command created event. It will be called when the button is clicked.
    futil.add_handler(cmd_def.commandCreated, command_created)

    # ******** Add a button into the UI so the user can run the command. ********
    # Get the target workspace the button will be created in.
    workspace = ui.workspaces.itemById(WORKSPACE_ID)

    # Get the panel the button will be created in.
    panel = workspace.toolbarPanels.itemById(PANEL_ID)

    # Create the button command control in the UI after the specified existing command.
    control = panel.controls.addCommand(cmd_def, COMMAND_BESIDE_ID, False)

    # Specify if the command is promoted to the main toolbar. 
    control.isPromoted = IS_PROMOTED


# Executed when add-in is stopped.
def stop():
    # Get the various UI elements for this command
    workspace = ui.workspaces.itemById(WORKSPACE_ID)
    panel = workspace.toolbarPanels.itemById(PANEL_ID)
    command_control = panel.controls.itemById(CMD_ID)
    command_definition = ui.commandDefinitions.itemById(CMD_ID)

    # Delete the button command control
    if command_control:
        command_control.deleteMe()

    # Delete the command definition
    if command_definition:
        command_definition.deleteMe()


# Function that is called when a user clicks the corresponding button in the UI.
# This defines the contents of the command dialog and connects to the command related events.
def command_created(args: adsk.core.CommandCreatedEventArgs):
    # General logging for debug.
    futil.log(f'{CMD_NAME} Command Created Event')

    # https://help.autodesk.com/view/fusion360/ENU/?contextId=CommandInputs
    inputs = args.command.commandInputs

    # Create a value input field for the width
    defaultLengthUnits = app.activeProduct.unitsManager.defaultLengthUnits
    default_value = adsk.core.ValueInput.createByString('40')
    inputs.addValueInput('width_value_input', 'Back Width', defaultLengthUnits, default_value)

    # Create a value input field for the height
    defaultLengthUnits = app.activeProduct.unitsManager.defaultLengthUnits
    default_value = adsk.core.ValueInput.createByString('30')
    inputs.addValueInput('height_value_input', 'Back Height', defaultLengthUnits, default_value)

    # boolean input for whether to create the back and cut
    inputs.addBoolValueInput('tools_only', 'Tools Only', True)

    # TODO Connect to the events that are needed by this command.
    futil.add_handler(args.command.execute, command_execute, local_handlers=local_handlers)
    futil.add_handler(args.command.inputChanged, command_input_changed, local_handlers=local_handlers)
    futil.add_handler(args.command.executePreview, command_preview, local_handlers=local_handlers)
    futil.add_handler(args.command.validateInputs, command_validate_input, local_handlers=local_handlers)
    futil.add_handler(args.command.destroy, command_destroy, local_handlers=local_handlers)


def create_user_parm_if_needed(param):

    print("Definining parameter ", param, "\n")

    fRef = userParams.itemByName(param.name)

    if fRef is None:
        fRef=userParams.add(param.name,
                            adsk.core.ValueInput.createByString(str(param.value)),
                            param.unit, param.desc)
        
    return (param.name, FParm(param.value, fRef))

# This event handler is called when the user clicks the OK button in the command dialog or 
# is immediately called after the created event not command inputs were created for the dialog.
def command_execute(args: adsk.core.CommandEventArgs):

    try:

        # Get a reference to your command's inputs.
        inputs = args.command.commandInputs
        width_value_input: adsk.core.TextBoxCommandInput = inputs.itemById('width_value_input')
        height_value_input: adsk.core.ValueCommandInput = inputs.itemById('height_value_input')
        tool_only_input = inputs.itemById('tools_only')
    
        backHeight = max(2.5, height_value_input.value)
        backWidth = max(width_value_input.value, distanceBetweenSlots)
        slotCount = math.floor(backWidth/distanceBetweenSlots)
        backThickness = 0.65
    
    
        modelUserParms = [
            UserParm("width", width_value_input.value, "cm", "width of the model"),
            UserParm("height", height_value_input.value, "cm", 'height of the model'),
            UserParm("tools_only", 1 if tool_only_input.value else 0, "", 'who knows'),
            UserParm("backHeight", f'max(height;2.4cm)', "cm", 'height of the back'),
            UserParm("backWidth", f'max(width;{distanceBetweenSlotsParm})', "cm", 'height of the back'),
            UserParm("slotCount", 'floor(backWidth/distanceBetweenSlots)', '', 'number of slots'),
            UserParm("backThickness", "0.65", "cm", 'thickness of the back')
        ]

        dUserParms = dict(map(create_user_parm_if_needed, generalModelUserParms + modelUserParms))
    
    
        slot_tool = create_slot()
        
        # Move the tool to the middle slot location
        bodies = adsk.core.ObjectCollection.create()
        bodies.add(slot_tool)
    
        # offset to the edge location, because symmetrical patterns aren't working correctly in the API
        slotXShift = (distanceBetweenSlots * ( 1 - slotCount))/2
        slotXshiftStr = "(distanceBetweenSlots * ( 1 - slotCount))/2"

        ax = adsk.core.ValueInput.createByString("distanceBetweenSlots * ( 1 - slotCount)/2")
        ay = adsk.core.ValueInput.createByString("backThickness - 0.5")
        az = adsk.core.ValueInput.createByString("backHeight - 1.3")
        assert(ax)
        assert(ay)
        assert(az)

        print("Values", ax, ay, az, "\n")

#        vector = adsk.core.Vector3D.create(ax, ay, az)
        vector = adsk.core.Vector3D.create(distanceBetweenSlots * ( 1 - slotCount)/2,
                                           backThickness - 0.5, backHeight - 1.3)


        transform = adsk.core.Matrix3D.create()
        transform.translation = vector
    
        moveFeats = features.moveFeatures
        moveFeatureInput = moveFeats.createInput2(bodies)
        moveFeatureInput.defineAsFreeMove(transform)
        moveFeats.add(moveFeatureInput)
    
        #  Make more slots
        rectangularPatterns = features.rectangularPatternFeatures
        patternInput = rectangularPatterns.createInput(
            bodies, 
            root.xConstructionAxis,
            adsk.core.ValueInput.createByString("slotCount"),
            adsk.core.ValueInput.createByString("distanceBetweenSlots"), 
            adsk.fusion.PatternDistanceType.SpacingPatternDistanceType)
    
        slotPattern = rectangularPatterns.add(patternInput)
        slotBodies = adsk.core.ObjectCollection.create()
        for body in slotPattern.bodies:
                slotBodies.add(body)
    
    
        if not tool_only_input.value:
        # Make the overall shape
            back = create_back_cube("backWidth", "backThickness", "backHeight")
    
            # Subtract the slot tool
            combineFeatures = features.combineFeatures
    
            input: adsk.fusion.CombineFeatureInput = combineFeatures.createInput(back, slotBodies)
            input.isNewComponent = False
            input.isKeepToolBodies = False
            input.operation = adsk.fusion.FeatureOperations.CutFeatureOperation
            combineFeature = combineFeatures.add(input)


    except:
        if ui:
            ui.messageBox('Failed:\n{}'.format(traceback.format_exc()))

        app.log(f'Failed:\n{traceback.format_exc()}')
    
    
def create_point_dimensions_xy(sketch, x, y):
    global sketchAxes

    point = adsk.core.Point3D.create(0,  0,0)
    point0 = adsk.core.Point3D.create(0, 0,0)
    sketchPoints = sketch.sketchPoints
    sketchP = sketchPoints.add(point)
    sketchP0 = sketchPoints.add(point0)
    
    sketchDimensions = sketch.sketchDimensions
    
    if x:
        dimx = sketchDimensions.addDistanceDimension(sketchP, sketchP0, 
                                                     adsk.fusion.DimensionOrientations.HorizontalDimensionOrientation,
                                                     adsk.core.Point3D.create(0, 0, 0))
        dimx.parameter.expression = x
    else:
        sketch.geometricConstraints.addCoincident(sketchP, sketchAxes.y)

    if y:
        dimy = sketchDimensions.addDistanceDimension(sketchP, sketchP0,
                                                     adsk.fusion.DimensionOrientations.VerticalDimensionOrientation,
                                                     adsk.core.Point3D.create(0, 0, 0))
        dimy.parameter.expression = y
    else:
        sketch.geometricConstraints.addCoincident(sketchP, sketchAxes.x)
        
    return sketchP



def create_centered_rectangle_dimensions(sketch, centerPoint, widthStr, heightStr,
                                         labelPointX=None, labelPointY=None):
    # centerPoint is a sketch point

    if labelPointX == None:
        labelPointX = adsk.core.Point3D.create(0, 0, 0)
    if labelPointY == None:
        labelPointY = adsk.core.Point3D.create(0, 0, 0)
    labelPointCX = adsk.core.Point3D.create(0, 0, 0)
    labelPointCY = adsk.core.Point3D.create(0, 0, 0)
    # we need to create dimensions for:
    #    centerPoint,
    #    width, and
    #    height

    lines = sketch.sketchCurves.sketchLines
    dimensions = sketch.sketchDimensions
    point = adsk.core.Point3D.create(10, 10, 0)

    # create rectangle
    rect = lines.addCenterPointRectangle(centerPoint.geometry, point)

    # Apply dimensions using user parameters
    dimensions.addDistanceDimension(rect[0].startSketchPoint, rect[0].endSketchPoint, 
                                    adsk.fusion.DimensionOrientations.HorizontalDimensionOrientation, 
                                    labelPointX).parameter.expression = widthStr
    dimensions.addDistanceDimension(rect[1].startSketchPoint, rect[1].endSketchPoint, 
                                    adsk.fusion.DimensionOrientations.VerticalDimensionOrientation, 
                                    labelPointY).parameter.expression = heightStr

    cornerx = dimensions.addDistanceDimension(rect[0].startSketchPoint, centerPoint, 
                                    adsk.fusion.DimensionOrientations.HorizontalDimensionOrientation,
                                                     labelPointCX)
    cornerx.parameter.expression = widthStr + "/2"

    cornery = dimensions.addDistanceDimension(rect[1].startSketchPoint, centerPoint, 
                                    adsk.fusion.DimensionOrientations.VerticalDimensionOrientation,
                                                     labelPointCY)
    cornery.parameter.expression = heightStr + "/2"

    return rect


def create_axis(sketch, isx, len):
    # isx true for x axis
    if isx:
        point1 = adsk.core.Point3D.create(-len, 0, 0)
        point2 = adsk.core.Point3D.create(len, 0, 0)
        constr = sketch.geometricConstraints.addHorizontal
    else:
        point1 = adsk.core.Point3D.create(0, -len, 0)
        point2 = adsk.core.Point3D.create(0, len, 0)
        constr = sketch.geometricConstraints.addVertical
        
    axis = sketch.sketchCurves.sketchLines.addByTwoPoints(
        point1, point2)

    sketch.geometricConstraints.addCoincident(sketch.originPoint, axis)
    
    # set the horizontal/vertical constraint
    constr(axis)
    

    ##sketch.originPoint
    axis.isConstruction = True
    return axis


def create_back_cube(w, d, h):
    global sketchAxes
    sketch = root.sketches.add(root.xYConstructionPlane)
    sketch.name = "Back Profile"
    wDim = adsk.core.ValueInput.createByString(w)
    dDim = adsk.core.ValueInput.createByString(d)

    sketchAxes = SketchAxes(create_axis(sketch, True, 20),
                            create_axis(sketch, False, 20))
    
    centerPoint = create_point_dimensions_xy(sketch, None, d + "/2.0")

    #    cornerPoint = create_point_dimensions_xy(sketch, w + "/2.0", d)

    rect = create_centered_rectangle_dimensions(sketch, centerPoint, w, d, None, None)

    #   sketch.sketchCurves.sketchLines.addCenterPointRectangle(centerPoint.geometry, cornerPoint)    


    profile = sketch.profiles.item(0)
    distance = adsk.core.ValueInput.createByString(h)
    cubeExtrude = features.extrudeFeatures.addSimple(profile, distance, adsk.fusion.FeatureOperations.NewBodyFeatureOperation)

    backBody = cubeExtrude.bodies.item(0)
    backBody.name = "Back"

    return backBody


def create_slot():

    slotSketch = root.sketches.add(root.xYConstructionPlane)
    slotSketch.name = "Slot Profile"

    profilePoints = [adsk.core.Point3D.create(x, y, 0) for x, y in
                     [[0,0],
                      [dotDiameter.value,0],
                      [dotDiameter.value,0.12121],
                      [0.765,0.3712],
                      [0.765,0.5],
                      [0,0.5]]]

    drawPolyline(slotSketch, profilePoints)

 
    slotProfile = slotSketch.profiles.item(0)

    lines = slotSketch.sketchCurves.sketchLines
    axisLine = root.yConstructionAxis

    revolveFeats = features.revolveFeatures
    revolveInput = revolveFeats.createInput(slotProfile, axisLine, adsk.fusion.FeatureOperations.NewBodyFeatureOperation)

    # Set the rotation angle (90 degrees in radians)
    revolveInput.setAngleExtent(False, adsk.core.ValueInput.createByReal(3.14159265359 * 0.5))

    # Execute the revolve
    revolveFeats.add(revolveInput)

    # Extrude the slot length
    extrudes = features.extrudeFeatures
    ############ this is the extrude that needs to be parametrized

    distance = adsk.core.ValueInput.createByString("backHeight * -1")
    extrude1 = extrudes.addSimple(slotProfile,
                                  distance,
                                  adsk.fusion.FeatureOperations.JoinFeatureOperation)        
    # Get the extrusion body
    body1 = extrude1.bodies.item(0)
    body1.name = "Slot"

    inputEntites = adsk.core.ObjectCollection.create()
    inputEntites.add(body1)
    mirrorFeatures = features.mirrorFeatures
    mirrorInput = mirrorFeatures.createInput(inputEntites, root.yZConstructionPlane)
    mirrorInput.isCombine = True
        
    # Create the mirror feature
    mirrorFeature = mirrorFeatures.add(mirrorInput)

    # TODO add conditional for onramp
    rampFeature = createOnramp()

#    rampSpacing = distanceBetweenSlots * onRampEveryXSlots
#    rampQuantity = math.floor(backHeight/rampSpacing)
    
    patternCollection = adsk.core.ObjectCollection.create()
    patternCollection.add(rampFeature)
    rectangularPatterns = features.rectangularPatternFeatures
    patternInput = rectangularPatterns.createInput(
        patternCollection, 
        root.zConstructionAxis,
        adsk.core.ValueInput.createByString("floor(backHeight/(distanceBetweenSlots * onRampEveryXSlots))"),
        adsk.core.ValueInput.createByString("(-distanceBetweenSlots) * onRampEveryXSlots"), 
        adsk.fusion.PatternDistanceType.SpacingPatternDistanceType)
    rectangularPattern = rectangularPatterns.add(patternInput)

    # TODO add conditional for dimple
    createDimple()

    return body1

def createOnramp():
    # Create the sketch for the cylinder
    rampSketch = root.sketches.add(root.xZConstructionPlane)
    rampSketch.name = "Ramp Sketch"

    circles = rampSketch.sketchCurves.sketchCircles
    circle1 = circles.addByCenterRadius(adsk.core.Point3D.create(0,2, 0), dotDiameter.value*2)
    circleDim = rampSketch.sketchDimensions.addDiameterDimension(circle1, adsk.core.Point3D.create(1.2, 1.2, 0)) 
    # get ModelParameter
    modelPrm: adsk.fusion.ModelParameter = circleDim.parameter

    # Set user parameter name in ModelParameter
    modelPrm.expression = dotDiameter.name + "*2"

    # extrude into cylinder
    extrudes = features.extrudeFeatures
    distance = adsk.core.ValueInput.createByReal(0.5)
    rampExtrude = extrudes.addSimple(rampSketch.profiles.item(0), distance, adsk.fusion.FeatureOperations.JoinFeatureOperation)

    return rampExtrude

def createDimple():
    dimpleSketch = root.sketches.add(root.yZConstructionPlane)
    dimpleSketch.name = "Dimple sketch"

    profilePoints = [adsk.core.Point3D.create(x, y, 0) for x, y in [[0,0],[0,0.15],[0.15,0]]]
    drawPolyline(dimpleSketch, profilePoints)

    profile = dimpleSketch.profiles.item(0)
    axisLine = root.yConstructionAxis

    revolveFeats = features.revolveFeatures

    revolveInput = revolveFeats.createInput(profile, axisLine, adsk.fusion.FeatureOperations.CutFeatureOperation)
    # Revolve the dimple
    revolveInput.setAngleExtent(False, adsk.core.ValueInput.createByReal(3.14159265359 * 2))

    # Execute the revolve
    revolveFeats.add(revolveInput)


def drawPolyline(
    skt :adsk.fusion.Sketch,
    pnts :list):

    count = len(pnts)
    pnts.append(pnts[0])

    lines = skt.sketchCurves.sketchLines

    skt.isComputeDeferred = True
    [lines.addByTwoPoints(pnts[i], pnts[i + 1]) for i in range(count)]
    skt.isComputeDeferred = False

# This event handler is called when the command needs to compute a new preview in the graphics window.
def command_preview(args: adsk.core.CommandEventArgs):
    # General logging for debug.
    futil.log(f'{CMD_NAME} Command Preview Event')
    inputs = args.command.commandInputs


# This event handler is called when the user changes anything in the command dialog
# allowing you to modify values of other inputs based on that change.
def command_input_changed(args: adsk.core.InputChangedEventArgs):
    changed_input = args.input
    inputs = args.inputs

    # General logging for debug.
    futil.log(f'{CMD_NAME} Input Changed Event fired from a change to {changed_input.id}')


# This event handler is called when the user interacts with any of the inputs in the dialog
# which allows you to verify that all of the inputs are valid and enables the OK button.
def command_validate_input(args: adsk.core.ValidateInputsEventArgs):
    # General logging for debug.
    futil.log(f'{CMD_NAME} Validate Input Event')

    inputs = args.inputs
    
    # Verify the validity of the input values. This controls if the OK button is enabled or not.
    valueInput = inputs.itemById('width_value_input')
    if valueInput.value >= 0:
        args.areInputsValid = True
    else:
        args.areInputsValid = False
        
       
    valueInput = inputs.itemById('height_value_input')
    if valueInput.value >= 0:
        args.areInputsValid = True
    else:
        args.areInputsValid = False
        

# This event handler is called when the command terminates.
def command_destroy(args: adsk.core.CommandEventArgs):
    # General logging for debug.
    futil.log(f'{CMD_NAME} Command Destroy Event')

    global local_handlers
    local_handlers = []
