import logging
import re
import warnings
from abc import ABC, abstractmethod
from copy import copy
from typing import Tuple

import astropy.coordinates
import numpy as np
import pandas
from astropy import wcs
from astropy.coordinates import SkyCoord, Angle, ICRS
import astropy.units as u
from astropy.io import fits
from astropy.table import Table
from astropy.time import Time
from astropy.visualization import astropy_mpl_style
from astropy.visualization.wcsaxes import SphericalCircle, Quadrangle, WCSAxes
from astroquery.gaia import Gaia
from astroquery.simbad import Simbad
from matplotlib import pyplot as plt
from matplotlib.collections import PathCollection
from matplotlib.lines import Line2D
from matplotlib.patches import Rectangle
from matplotlib.transforms import Bbox
import mplcursors
from DataToolkit.Decorators import ignore_warnings, plotting


logger = logging.getLogger("astroquery")
logger.setLevel(logging.CRITICAL)

# Create an InputError exception class with a message
class InputError(Exception):
    def __init__(self, message):
        self.message = message

class QueryError(Exception):
    def __init__(self, message):
        self.message = message

class Searcher(ABC):
    boolean_operators = ["&", "|"]
    def __init__(self, search_parameters):
        """
        Abstract class for searching a database.

        Parameters
        ----------
        search_parameters : dict
            Dictionary of search parameters.

        Attributes
        ----------
        database_name : str
            Name of the database.
        default_votable_fields : list
            List of default votable fields.
        search_coordinates : astropy.coordinates.SkyCoord
            Coordinates to search around.
        search_type : str
            Type of search to perform.
        search_input : str
            Input to use for the search.
        result_table : astropy.table.Table
            Table of results from the search.
        result_dataframe : pandas.DataFrame
            DataFrame of results from the search.
        decimal_coordinate_keys : dict
            Dictionary of decimal coordinate keys.
        source_name_key : dict
            Dictionary of the key used to identify the source from the database.
        default_equinox : float
            Default equinox to use for the search.
        default_epoch : float
            Default epoch to use for the search.
        default_frame : str
            Default frame to use for the search.
        """

        self.database_name = None
        self.default_votable_fields = []
        self.search_coordinates, self.search_type, self.search_input = self.checkInput(search_parameters=search_parameters)
        self.result_table = None
        self.result_dataframe = None
        self.decimal_coordinate_keys = {"RA": None, "DEC": None}
        self.source_name_key = {"Source Name": None}
        self.default_equinox = None
        self.default_epoch = None
        self.default_frame = None

    @abstractmethod
    def getQuery(self, args):
        """
        Abstract method to get the query result from the database.

        Parameters
        ----------
        args : tuple
         arguments to pass to the database query.
        """

        # Each child class must implement this method

        # Perform some manipulation of the arguments as needed
        # args = function(args)

        # Get the query result from of the child class's associated database
        # database_query_result = database_query.getQuery(args)

        # return database_query_result
        pass

    def applyQueryCriteria(self, conditions):
        """
        Apply conditions to the result table.

        Parameters
        ----------
        conditions : str or tuple[str]

        Returns
        -------
        astropy.table.Table
            Table of results from the conditional search.
        """

        if(self.result_table is None or len(self.result_table) == 0):
            return self.result_table

        if (conditions is None):
            return copy(self.result_table)

        if (isinstance(conditions, str)):
            conditions = [conditions]
        elif (not isinstance(conditions, list) and not isinstance(conditions, tuple)):
            raise InputError(f"Invalid conditions argument provided to {self.__class__.__name__}: {conditions} of type {type(conditions)}")

        modified_result_table = copy(self.result_table)
        modified_result_dataframe = copy(self.result_dataframe)
        for condition in conditions:
            if(modified_result_table is None or len(modified_result_table) == 0):
                self.result_table = modified_result_table
                self.result_dataframe = modified_result_dataframe
                return copy(modified_result_table)
            boolean_list = self.checkCondition(modified_result_table, condition)
            modified_result_dataframe = modified_result_dataframe[boolean_list]
            modified_result_table = modified_result_table[boolean_list]
        self.result_table = modified_result_table
        self.result_dataframe = modified_result_dataframe
        return copy(self.result_table)

    @abstractmethod
    def getConditionalQuery(self, conditions=(), args=()):
        """
        Abstract method to get the query result from the database.

        Parameters
        ----------
        conditions : str or tuple[str]
            Conditions to apply to the query.
        args : tuple
            Arguments to pass to the database query.

        Returns
        -------
        astropy.table.Table
            Table of results from the conditional search.
        """

        # Each child class must implement this method

        # Perform some manipulation of the arguments as needed
        # args = function(args)

        # Get the query
        self.getQuery(args)

        # Apply the conditions
        return self.applyQueryCriteria(conditions)

    @staticmethod
    def applyUnit(value, unit):
        """
        Apply a unit to a value.

        Parameters
        ----------
        value : float or astropy.units.Quantity
            Value to apply the unit to.
        unit : astropy.units.Unit
            Unit to apply to the value.

        Returns
        -------
        astropy.units.Quantity
            Value with the unit applied and converted if necessary.
        """

        try:
            if(value is None):
                return None

            if(unit is None):
                raise InputError(f"No unit provided to apply to value: {value}")

            if(isinstance(value, list) or isinstance(value, tuple)):
                return [Searcher.applyUnit(v, unit) for v in value]

            if (isinstance(value, u.Quantity)):
                return value.to(unit=unit)
            else:
                angle_units = [u.hourangle, u.deg, u.arcmin, u.arcsec, u.mas]
                angle_unit_booleans = [unit == angle_unit for angle_unit in angle_units]
                if (any(angle_unit_booleans)):
                    return Angle(value, unit=unit)
                else:
                    return u.Quantity(value, unit=unit)
        except ValueError:
            raise InputError(f"Invalid value provided, \"{value}\" of type: {type(value).__name__}")

    def getUnit(self, table_field):
        """
        Get the unit of a table field.

        Parameters
        ----------
        table_field : str
            Field of the table to get the unit of.

        Returns
        -------
        astropy.units.Unit
            Unit of the table field.
        """

        if(self.result_table is None):
            result_table = self.getQuery()
        else:
            result_table = self.result_table
        return result_table.columns[table_field].unit

    @staticmethod
    def fixTableUnits(table):
        """
        Fix the units of a table.

        Parameters
        ----------
        table : astropy.table.Table
            Table to fix the units of.

        Returns
        -------
        astropy.table.Table
            Table with the units fixed.

        Notes
        -----
        This method is used to fix the units of a table after it has been
        retrieved from the database. This is necessary because the units of
        the table are not correct for some angle units.
        """

        for column in table.columns:
            column_unit = table[column].unit
            if (column_unit is not None):
                if (isinstance(column_unit, astropy.units.UnrecognizedUnit)):
                    column_unit_string = str(column_unit).replace("\"", "")
                    if (column_unit_string == "h:m:s"):
                        column_unit = u.hourangle
                    elif (column_unit_string == "d:m:s"):
                        column_unit = u.deg
                table[column].unit = column_unit
            else:
                if(column == "dist"):
                    table[column].unit = u.deg

        return table

    def checkInput(self, search_parameters):
        """
        Check the input parameters for the search.

        Parameters
        ----------
        search_parameters : dict
            Dictionary of search parameters.

        Returns
        -------
        search_coordinates
            Coordinates of the search.
        valid_search_type
            Valid search type.
        search_input
            Input for the search.
        """

        # Get the search coordinates
        search_coordinates = search_parameters.get("Coordinates", None)

        # Get the reference epoch of the search coordinates
        search_coordinates_equinox = search_parameters.get("Equinox", None)
        if(search_coordinates_equinox is not None):
            search_coordinates_equinox = Time(search_coordinates_equinox, format="jyear")

        search_coordinates_epoch = search_parameters.get("Epoch", None)
        if(search_coordinates_epoch is not None):
            search_coordinates_epoch = Time(search_coordinates_epoch, format="jyear")

        search_coordinates_frame = search_parameters.get("Frame", None)

        if (search_coordinates is None):
            raise InputError(f"No search coordinates provided to {self.__class__.__name__}")

        # Check if the search coordinates are valid
        if (isinstance(search_coordinates, SkyCoord)):
            if (search_coordinates_equinox is not None and search_coordinates.equinox != search_coordinates_equinox):
                raise InputError(f"Invalid search coordinates provided to {self.__class__.__name__}: {search_coordinates} with equinox {search_coordinates.equinox} (should be {search_coordinates_equinox})")

            if(search_coordinates_epoch is not None and search_coordinates.obstime != search_coordinates_epoch):
                raise InputError(f"Invalid search coordinates provided to {self.__class__.__name__}: {search_coordinates} with epoch {search_coordinates.obstime} (should be {search_coordinates_epoch})")

            if(search_coordinates_frame is not None and search_coordinates.frame.name != search_coordinates_frame):
                raise InputError(f"Invalid search coordinates provided to {self.__class__.__name__}: {search_coordinates} with frame {search_coordinates.frame.name} (should be {search_coordinates_frame})")

            search_coordinates = search_coordinates.transform_to("icrs")

        elif(isinstance(search_coordinates, list) or isinstance(search_coordinates, tuple)):
            if(len(search_coordinates) != 2):
                raise InputError(f"Invalid search coordinates provided to {self.__class__.__name__}: {search_coordinates} of length {len(search_coordinates)} (should be 2)")

            ra_angle = self.applyUnit(search_coordinates[0], unit=u.deg)
            dec_angle = self.applyUnit(search_coordinates[1], unit=u.deg)

            if(search_coordinates_equinox is None):
                search_coordinates_equinox = Time(self.default_equinox, format="jyear")

            if(search_coordinates_epoch is None):
                search_coordinates_epoch = Time(self.default_epoch, format="jyear")

            if(search_coordinates_frame is None):
                search_coordinates_frame = self.default_frame

            search_coordinates = SkyCoord(ra_angle, dec_angle, unit=u.deg, frame=search_coordinates_frame, obstime=search_coordinates_epoch, equinox=search_coordinates_equinox)
        else:
            raise InputError(f"Invalid search coordinates provided to {self.__class__.__name__}: {search_coordinates} of type {type(search_coordinates)}")

        # Get the search type
        search_type = search_parameters.get("Type", None)

        if(search_type is None):
            raise InputError(f"No search type provided to {self.__class__.__name__}")
        elif(not isinstance(search_type, str)):
            raise InputError(f"Invalid search type provided to {self.__class__.__name__}: {search_type} of type {type(search_type)}")

        # Setup valid search type name(s) and associated input name(s)
        search_type_dict = {
            ("Cone", "Cone Search"): ["Radius", "radius", "r"],
            ("Box", "Box Search"): ["FOV", "fov", "field of view"]
        }

        valid_search_type_found = False
        valid_search_type_input = None
        valid_search_type = None
        for search_types, search_type_inputs in search_type_dict.items():
            if (search_type in search_types):
                valid_search_type = search_type
                valid_search_type_found = True
                search_type_inputs_booleans = [search_parameters.get(search_type_input, None) is not None for search_type_input in search_type_inputs]
                search_type_inputs_boolean_sum = sum(search_type_inputs_booleans)
                if (search_type_inputs_boolean_sum > 1):
                    error_message = f"Too many {search_type} inputs provided to {self.__class__.__name__}: "
                    for found_search_type_input in np.array(search_type_inputs)[search_type_inputs_booleans]:
                        error_message += f"{found_search_type_input}, "
                    error_message = error_message[:-2]
                    raise InputError(error_message)
                elif(search_type_inputs_boolean_sum == 0):
                    error_message = f"No inputs, or no valid inputs, provided to {self.__class__.__name__}, expected one of: "
                    for search_type_input in search_type_inputs:
                        error_message += f"{search_type_input}, "
                    error_message = error_message[:-2]
                    raise InputError(error_message)
                else:
                    valid_search_type_input = np.array(search_type_inputs)[search_type_inputs_booleans][0]
                break
            else:
                continue

        if(not valid_search_type_found):
            raise InputError(f"Unknown search type provided to {self.__class__.__name__}: {search_type}")

        search_input = search_parameters.get(valid_search_type_input)

        search_input = self.applyUnit(search_input, unit=u.arcsec)

        return search_coordinates, valid_search_type, search_input

    @staticmethod
    def createWCS(coordinates: SkyCoord):
        """
        Create a WCS object from a SkyCoord object

        Parameters
        ----------
        coordinates : SkyCoord
            The coordinates to create the WCS object from

        Returns
        -------
        WCS
            The WCS object created from the coordinates
        """

        if(not isinstance(coordinates, SkyCoord)):
            raise InputError(f"Coordinates provided were not of type SkyCoord: {type(coordinates)}")


        ra = coordinates.ra.deg
        dec = coordinates.dec.deg
        # Create a WCS object
        fits_header = fits.Header()
        fits_header['CTYPE1'] = 'RA---TAN'
        fits_header['CTYPE2'] = 'DEC--TAN'
        fits_header['CDELT1'] = -0.000763888888889
        fits_header['CDELT2'] = 0.000763888888889
        fits_header['CRVAL1'] = ra  # Reference RA value in degrees
        fits_header['CRVAL2'] = dec  # Reference DEC value in degrees
        fits_header['CRPIX1'] = 0  # Reference pixel in X (RA) direction
        fits_header['CRPIX2'] = 0  # Reference pixel in Y (DEC) direction

        default_wcs = wcs.WCS(fits_header)
        return default_wcs

    @staticmethod
    def getFOVBox(center_coordinates: SkyCoord, FOV, wcs_ax: WCSAxes, **kwargs):
        """
        Get the FOV box for a given center coordinate and FOV

        Parameters
        ----------
        center_coordinates : SkyCoord
            The center coordinates of the FOV

        FOV : float
            The FOV of the box

        wcs_ax : WCSAxes
            The WCSAxes object to get the FOV box for

        Returns
        -------
        Quadrangle
            The FOV box on the sky
        """

        if(not isinstance(center_coordinates, SkyCoord)):
            raise InputError(f"Center coordinates are not of the type SkyCoord: {type(center_coordinates)}")

        FOV = Searcher.applyUnit(FOV, unit=u.arcsec)

        resolution = kwargs.get("resolution", 300)
        kwargs.pop("resolution", None)
        world_fov_quadrangle = Quadrangle((center_coordinates.ra - FOV/2, center_coordinates.dec - FOV/2), FOV, FOV, resolution=resolution, transform=wcs_ax.get_transform("icrs"), **kwargs)

        return world_fov_quadrangle

    @staticmethod
    def getFOVLimits(center_coordinates: SkyCoord, FOV, wcs_ax: WCSAxes) -> Tuple[float, float, float, float]:
        """
        Get the FOV limits for a given center coordinate and FOV

        Parameters
        ----------
        center_coordinates : SkyCoord
            The center coordinates of the FOV

        FOV : float
            The FOV of the box

        wcs_ax : WCSAxes
            The WCSAxes object to get the FOV box for

        Returns
        -------
        ra_min : float
            The minimum RA value of the FOV box
        ra_max : float
            The maximum RA value of the FOV box
        dec_min : float
            The minimum DEC value of the FOV box
        dec_max : float
            The maximum DEC value of the FOV box
        """

        world_fov_quadrangle = Searcher.getFOVBox(center_coordinates, FOV, wcs_ax)
        vertices = world_fov_quadrangle.get_xy()
        ra_min = min(vertices[:,0])
        ra_max = max(vertices[:,0])
        dec_min = min(vertices[:,1])
        dec_max = max(vertices[:,1])

        return ra_min, ra_max, dec_min, dec_max

    @plotting
    def plotEntries(self, **kwargs):
        """
        Plot the entries in the result table

        Parameters
        ----------
        kwargs : dict
            Keyword arguments to pass to the plotting function or the plotting decorator.
        """

        if (self.result_table is None):
            print("Cannot plot entries. Result table is None.")
            return None
        fig_size = kwargs.pop("fig_size", (10,10))
        style = kwargs.pop("style", astropy_mpl_style)

        fig = plt.gcf()
        fig.set_size_inches(fig_size)
        plt.style.use(style)

        # Create a WCS object
        default_wcs = self.createWCS(self.search_coordinates)

        ax = fig.add_subplot(111, projection=default_wcs)
        search_ra = self.search_coordinates.ra.deg
        search_dec = self.search_coordinates.dec.deg

        def setAxesLimits(ra_min, ra_max, dec_min, dec_max):
            xlim_skycoord = (SkyCoord(ra_min, search_dec, unit="deg"), SkyCoord(ra_max, search_dec, unit="deg"))
            xlim_pixel = (float(default_wcs.world_to_pixel(xlim_skycoord[0])[0]),
                          float(default_wcs.world_to_pixel(xlim_skycoord[1])[0]))
            ax.set_xlim(xlim_pixel)

            ylim_skycoord = (SkyCoord(search_ra, dec_min, unit="deg"), SkyCoord(search_ra, dec_max, unit="deg"))
            ylim_pixel = (float(default_wcs.world_to_pixel(ylim_skycoord[0])[1]),
                          float(default_wcs.world_to_pixel(ylim_skycoord[1])[1]))
            ax.set_ylim(ylim_pixel)

        ra_axis = ax.coords['ra']
        dec_axis = ax.coords['dec']

        ra_spacing = kwargs.pop("ra_spacing", None)
        dec_spacing = kwargs.pop("dec_spacing", None)

        ra_axis.set_major_formatter('dd:mm:ss')
        ra_axis.set_ticks(spacing=ra_spacing)
        dec_axis.set_major_formatter('dd:mm:ss')
        dec_axis.set_ticks(spacing=dec_spacing)

        add_grid = kwargs.pop("grid", False)
        ax.grid(add_grid)

        # Plot the search coordinates
        ax.plot(search_ra, search_dec, '+', transform=ax.get_transform('world'), color='green', markersize=10)

        # Plot the entries
        ra_key = self.decimal_coordinate_keys["RA"]
        dec_key = self.decimal_coordinate_keys["DEC"]
        name_key = self.source_name_key["Source Name"]

        s = kwargs.pop("s", 3)

        c = kwargs.pop("c", 'k')

        # Get the database name from the class name

        if (self.database_name != "" and self.database_name is not None):
            plt.title(f"{self.database_name} Query Results: {len(self.result_table)}")
        else:
            plt.title(f"Query Results: {len(self.result_table)}")

        separation = kwargs.pop("separation", None)

        source_labels = kwargs.pop("source_labels", False)

        source_labels_size = kwargs.pop("source_labels_size", 8)

        if (len(self.result_table) > 0):
            ax.scatter(list(self.result_table[ra_key]), list(self.result_table[dec_key]), transform=ax.get_transform('world'), s=s, c=c, *kwargs)

        if(source_labels):
            for i in range(len(self.result_table)):
                ax.text(self.result_table[ra_key][i], self.result_table[dec_key][i], self.result_table[name_key][i], transform=ax.get_transform('world'), fontsize=source_labels_size, ha='center')
        else:
            cursor = mplcursors.cursor(hover=True)

            def is_source_point(sel):
                return isinstance(sel.artist, PathCollection)

            def add_source_annotation(sel):
                if(is_source_point(sel)):
                    print(f"Currently hovering over: {self.result_table[name_key][sel.target.index]}")
                    sel.annotation.set_text(self.result_table[name_key][sel.target.index])
                    sel.annotation.set_fontsize(source_labels_size)
                    sel.annotation.set_bbox({"boxstyle": "round, pad=0.5", "edgecolor": "black", "facecolor": "white"})
                else:
                    sel.annotation.set_visible(False)

            cursor.connect("add", lambda sel: ignore_warnings(add_source_annotation)(sel))

        handles = []
        labels = []

        # Draw the dashed box around the field of view
        if (self.search_type == "Box" or self.search_type == "Box Search"):
            world_fov_rectangle = self.getFOVBox(self.search_coordinates, self.search_input, ax, edgecolor='blue', facecolor='none', linestyle='dashed')
            ax.add_patch(world_fov_rectangle)

            # Set the limits of the axes
            setAxesLimits(*self.getFOVLimits(self.search_coordinates, self.search_input, ax))

            # Add to existing legend
            handles.append(world_fov_rectangle)
            labels.append("Field of View")

        # Draw a dashed circle around the field of view
        elif (self.search_type == "Cone" or self.search_type == "Cone Search"):
            # Make a spherical circle
            search_circle = SphericalCircle((search_ra, search_dec) * u.deg, self.search_input, transform=ax.get_transform('world'), edgecolor='blue', facecolor='none', linestyle='dashed')
            ax.add_patch(search_circle)

            setAxesLimits(*self.getFOVLimits(self.search_coordinates, 2 * self.search_input, ax))

            handles.append(search_circle)
            labels.append("Search Radius")

        if (separation is not None):
            separation = self.applyUnit(separation, u.arcsec)
            separation_circle = SphericalCircle((search_ra, search_dec) * u.deg, separation, transform=ax.get_transform('world'), edgecolor='red', facecolor='none', linestyle='dashed')
            ax.add_patch(separation_circle)

            # Add to existing legend
            handles.append(separation_circle)
            labels.append("Separation")

        # Add the legend
        ax.legend(handles=handles, labels=labels, loc='upper right', fontsize=10)

        # Set the axis labels
        plt.xlabel("RA")
        plt.ylabel("DEC")

        # Reverse x-axis
        ax.invert_xaxis()


    @classmethod
    def getAllSubConditions(cls, conditional_string):
        """
        Returns a list of all the lowest-level sub-conditions in the conditional string

        Parameters
        ----------
        conditional_string : str
            The conditional string to parse

        Returns
        -------
        list[str]
            A list of all the sub-conditions in the conditional string
        """

        conditional_string = copy(conditional_string)

        if (conditional_string[0] == "(" and conditional_string[-1] == ")"):
            conditional_string = conditional_string[1:-1]

        if (any(boolean_operator in conditional_string for boolean_operator in cls.boolean_operators)):
            pattern = r"\(([^()]+)\)"
            sub_conditions = re.findall(pattern, conditional_string)
            # Add the parentheses back to the sub-conditions
            sub_conditions = ["(" + sub_condition + ")" for sub_condition in sub_conditions]
            return sub_conditions
        else:
            return ["(" + conditional_string + ")"]

    @classmethod
    def getSubConditions(cls, conditional_string):
        """
        Returns a list of the first-layer-deep sub-conditions in the conditional string

        Parameters
        ----------
        conditional_string : str

        Returns
        -------
        list[str]
            A list of the first-layer-deep sub-conditions in the conditional string
        """

        conditional_string = copy(conditional_string)

        if (conditional_string[0] == "(" and conditional_string[-1] == ")"):
            conditional_string = conditional_string[1:-1]

        if (any(boolean_operator in conditional_string for boolean_operator in cls.boolean_operators)):
            pattern = r"\(((?:[^()]*|\([^()]*\))*)\)"
            sub_conditions = re.findall(pattern, conditional_string)
            # Add the parentheses back to the sub-conditions
            sub_conditions = ["(" + sub_condition + ")" for sub_condition in sub_conditions]
            return sub_conditions
        else:
            return ["(" + conditional_string + ")"]

    @classmethod
    def safelySplitConditionalString(cls, conditional_string):
        """
        Safely splits a conditional string into its constituent parts

        Parameters
        ----------
        conditional_string : str
            The conditional string to split

        Returns
        -------
        list[str]
            A list of the constituent parts of the conditional string.
            The first element is the column name, the second element is the operator, and the third element is the value
        """

        if (any(boolean_operator in conditional_string for boolean_operator in cls.boolean_operators)):
            raise ValueError("Cannot safely split multi-conditional string with a boolean operator in it")

        if (conditional_string[0] == "(" and conditional_string[-1] == ")"):
            conditional_string = conditional_string[1:-1]
            if (conditional_string[0] == "(" and conditional_string[-1] == ")"):
                conditional_string = conditional_string[1:-1]
        split_conditional_string = conditional_string.split(" ")

        if (len(split_conditional_string) != 3):
            split_conditional_string = [split_conditional_string[0], split_conditional_string[1],
                                        " ".join(split_conditional_string[2:])]

        return split_conditional_string

    @classmethod
    def applyOperator(cls, operator, column_value, value):
        """
        Applies the operator to the column value and the value

        Parameters
        ----------
        operator : str
            The operator to apply
        column_value : float or str or astropy.units.Quantity
            The value of the column
        value : float or str or astropy.units.Quantity
            The value to compare the column value to

        Returns
        -------
        bool
            True if the operator is satisfied, False otherwise
        """

        if (operator == "<"):
            return column_value < value
        elif (operator == ">"):
            return column_value > value
        elif (operator == "<="):
            return column_value <= value
        elif (operator == ">="):
            return column_value >= value
        elif (operator == "="):
            return column_value == value
        elif (operator == "=="):
            return column_value == value
        elif (operator == "!="):
            return column_value != value
        elif ("~=" in operator):
            threshold_value = operator.split(",")[1]
            threshold = Searcher.applyUnit(threshold_value, unit=column_value.unit)
            return abs(column_value - value) <= threshold
        elif (operator == "between"):
            if(not isinstance(value, tuple) and not isinstance(value, list)):
                raise ValueError("Between operator requires a tuple or list.")

            if(len(value) != 2):
                raise ValueError("Between operator requires a tuple or list of two values.")

            value_min = min(value[0], value[1])
            value_max = max(value[0], value[1])

            return (column_value >= value_min) and (column_value <= value_max)
        elif (operator == "!between"):
            if (not isinstance(value, tuple) and not isinstance(value, list)):
                raise ValueError("Between operator requires a tuple or list.")

            if (len(value) != 2):
                raise ValueError("Between operator requires a tuple or list of two values.")

            value_min = value[0]
            value_max = value[1]

            return not ((column_value >= value_min) and (column_value <= value_max))
        elif (operator == "like"):
            return value.lower() in column_value.lower()
        elif (operator == "!like"):
            return value.lower() not in column_value.lower()
        elif (operator == "!in"):
            return value not in column_value
        elif (operator == "in"):
            return value in column_value
        else:
            raise ValueError("Invalid operator: " + operator)

    def checkCondition(self, table, conditional_string):
        """
        Checks if the conditional string is satisfied by the table

        Parameters
        ----------
        table : astropy.table.Table
            The table to check the conditional string against
        conditional_string : str
            The conditional string to check

        Returns
        -------
        bool
            True if the conditional string is satisfied by the table, False otherwise
        """

        sub_conditions = self.getSubConditions(conditional_string)
        sub_condition_boolean_lists = []
        for sub_condition in sub_conditions:
            sub_sub_conditions = self.getSubConditions(sub_condition)
            if (len(sub_sub_conditions) == 1):
                split_conditional_string = self.safelySplitConditionalString(sub_condition)
                column_name = split_conditional_string[0]
                operator = split_conditional_string[1]
                value = split_conditional_string[2]

                column_booleans = []
                column_values = []

                if (column_name == "ANGULAR_SEPARATION"):
                    column_unit = u.arcsec
                else:
                    column_unit = table[column_name].unit

                if (column_unit is not None):
                    if("[" in value and "]" in value):
                        value = value[1:-1]
                        value = value.split(",")
                    value = self.applyUnit(value, unit=column_unit)
                else:
                    column_type = table[column_name].dtype.type
                    value = column_type(value)

                if (column_name == "ANGULAR_SEPARATION"):
                    for ra, dec in zip(table[self.decimal_coordinate_keys["RA"]], table[self.decimal_coordinate_keys["DEC"]]):
                        object_coordinates = SkyCoord(ra, dec, unit="deg")
                        angular_separation = self.search_coordinates.separation(object_coordinates)
                        angular_separation = angular_separation.to(u.arcsec)
                        column_values.append(angular_separation)
                else:
                    column_values = list(table[column_name])
                for column_value in column_values:
                    try:
                        if ("|" in column_value):
                            column_bool_list = []
                            for sub_column_value in column_value.split("|"):
                                if (column_unit is not None):
                                    sub_column_value = self.applyUnit(sub_column_value, column_unit)
                                else:
                                    column_type = table[column_name].dtype.type
                                    sub_column_value = column_type(sub_column_value)
                                column_bool_list.append(self.applyOperator(operator, sub_column_value, value))
                            if ("!" in operator):
                                column_booleans.append(all(column_bool_list))
                            else:
                                column_booleans.append(any(column_bool_list))
                        else:
                            if (column_unit is not None):
                                column_value = self.applyUnit(column_value, column_unit)
                            else:
                                column_type = table[column_name].dtype.type
                                column_value = column_type(column_value)
                            column_booleans.append(self.applyOperator(operator, column_value, value))
                    except TypeError:
                        if (column_unit is not None):
                            column_value = self.applyUnit(column_value, column_unit)
                        else:
                            column_type = table[column_name].dtype.type
                            column_value = column_type(column_value)
                        column_booleans.append(self.applyOperator(operator, column_value, value))

                sub_condition_boolean_lists.append(column_booleans)
            else:
                sub_condition_boolean_lists.append(self.checkCondition(table, sub_condition))

        table_boolean_list = []
        if(len(sub_condition_boolean_lists) != 0):
            for table_index in range(len(sub_condition_boolean_lists[0])):
                modified_conditional_string = copy(conditional_string)
                for condition_index, sub_condition_boolean_list in enumerate(sub_condition_boolean_lists):
                    boolean_value = sub_condition_boolean_list[table_index]
                    modified_conditional_string = modified_conditional_string.replace(sub_conditions[condition_index], str(boolean_value))
                table_boolean_list.append(eval(modified_conditional_string))
        return table_boolean_list

    @classmethod
    def buildConditionalArgument(cls, column_name, operator, values, boolean_operator="|"):
        """
        Builds a conditional argument for the query

        Parameters
        ----------
        column_name : str
            The name of the column to build the conditional argument for
        operator : str
            The operator to use in the conditional argument
        values : list
            The values to use in the conditional argument
        boolean_operator : str
            The boolean operator to use to combine the conditional arguments
        """

        value_combine_exception_operators = ["between"]

        if (operator in value_combine_exception_operators):
            values = [values]
        else:
            if (not isinstance(values, list)):
                values = [values]

        conditional_args = []
        for i in range(len(values)):
            if(isinstance(values[i], list)):
                for j, value in enumerate(values[i]):
                    if(isinstance(value, u.Quantity)):
                        values[i][j] = f"{value}"
            conditional_arg = "(" + f"{column_name} {operator} {values[i]}".replace("'", "") + ")"
            conditional_args.append(conditional_arg)
        if (len(conditional_args) == 1):
            return "(" + conditional_args[0] + ")"
        else:
            return "(" + boolean_operator.join(conditional_args) + ")"

    @classmethod
    def combineConditionalArguments(cls, *conditional_args, boolean_operator="|"):
        """
        Combines multiple conditional arguments into a single conditional argument

        Parameters
        ----------
        conditional_args :
            The conditional arguments to combine
        boolean_operator : str
            The boolean operator to use to combine the conditional arguments

        Returns
        -------
        str
            The combined conditional argument
        """

        if (boolean_operator not in cls.boolean_operators):
            raise ValueError("The boolean operator must be one of: {}".format(cls.boolean_operators))

        if (len(conditional_args) == 1):
            return conditional_args[0]
        else:
            return "(" + boolean_operator.join(list(conditional_args)) + ")"

class SimbadSearcher(Searcher):
    def __init__(self, search_parameters):
        """
        Simbad Searcher class

        Parameters
        ----------
        search_parameters : dict
            The search parameters to use for the query
        """

        self.default_equinox = 2000.0
        self.default_epoch = 2000.0
        self.default_frame = "icrs"
        super().__init__(search_parameters=search_parameters)
        self.database_name = "Simbad"
        self.decimal_coordinate_keys = {"RA": "RA_d", "DEC": "DEC_d"}
        self.source_name_key = {"Source Name": "MAIN_ID"}

    def getQuery(self, votable_fields=()):
        """
        Gets the query for the Simbad search

        Parameters
        ----------
        votable_fields : tuple[str] or list[str]
            The VOTable fields to include in the query

        Returns
        -------
        astropy.table.Table
            The table of the query results
        """

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")

            simbad_query = Simbad()

            # Add some extra default VOTable fields to the query
            default_votable_fields = ['ra(d)', 'dec(d)', "ids", "otypes"]
            simbad_query.add_votable_fields(*default_votable_fields)

            # Add the VOTable fields to the query
            if(votable_fields is not None):
                if(isinstance(votable_fields, str)):
                    votable_fields = [votable_fields]
            else:
                votable_fields = []

            simbad_query.add_votable_fields(*votable_fields)

            # Construct the region query and get the result table
            result_table = None

            try:
                if (self.search_type == "Box" or self.search_type == "Box Search"):
                    # Get the ra and dec from the search coordinates
                    ra = self.search_coordinates.ra.deg
                    dec = self.search_coordinates.dec.deg
                    FOV = self.search_input.value

                    if (dec < 0):
                        dec = abs(dec)
                        box_search_string = f"region(box, {ra} -{dec}, {FOV}s {FOV}s)"
                    else:
                        box_search_string = f"region(box, {ra} +{dec}, {FOV}s {FOV}s)"
                    result_table = simbad_query.query_criteria(box_search_string)
                elif (self.search_type == "Cone" or self.search_type == "Cone Search"):
                    result_table = simbad_query.query_region(self.search_coordinates, radius=self.search_input)
            except Exception as e:
                raise QueryError(f"Query from {self.database_name} failed due to: {e}")

            if(result_table is None):
                self.result_table = Table()
                self.result_dataframe = pandas.DataFrame()
                return copy(self.result_table)

            result_table = self.fixTableUnits(result_table)

            self.result_table = copy(result_table)

            self.result_dataframe = copy(result_table.to_pandas())
            return copy(self.result_table)

    def getConditionalQuery(self, conditions=(), votable_fields=()):
        """
        Gets the query for the Simbad search with the given conditions

        Parameters
        ----------
        conditions : tuple[str]
            The conditions to apply to the query
        votable_fields : tuple[str]
            The VOTable fields to include in the query

        Returns
        -------
        astropy.table.Table
            The table of the query results with the given conditions
        """

        # Get the query
        votable_fields = list(votable_fields)
        extra_votable_fields = self.getRequiredVotableFields(conditions)
        votable_fields.extend(extra_votable_fields)
        votable_fields = list(set(votable_fields))
        self.getQuery(votable_fields)

        # Apply the conditions
        return self.applyQueryCriteria(conditions)

    @classmethod
    def getRequiredVotableFields(cls, conditional_strings):
        """
        Gets the required VOTable fields for the given conditions

        Parameters
        ----------
        conditional_strings : tuple
            The conditions to apply to the query

        Returns
        -------
        list[str]
            The list of required VOTable fields to include in the query for the given conditions
        """

        column_names = []
        votable_fields = []
        if (isinstance(conditional_strings, str)):
            conditional_strings = [conditional_strings]

        for conditional_string in conditional_strings:
            all_sub_conditions = cls.getAllSubConditions(conditional_string)
            for sub_condition in all_sub_conditions:
                split_conditional_string = cls.safelySplitConditionalString(sub_condition)
                column_name = split_conditional_string[0]
                if (column_name != "ANGULAR_SEPARATION"):
                    column_names.append(column_name)
            column_names = list(set(column_names))

        for column_name in column_names:

            default_fields = ["MAIN_ID", "RA", "DEC", "RA_PREC", "DEC_PREC", "COO_ERR_MAJA", "COO_ERR_MINA",
                              "COO_ERR_ANGLE", "COO_QUAL", "COO_WAVELENGTH", "COO_BIBCODE", "RA_d", "DEC_d", "IDS",
                              "SCRIPT_NUMBER_ID", "OTYPES"]
            default_fields = [default_field.lower() for default_field in default_fields]
            if (column_name.lower() not in votable_fields and column_name.lower() not in default_fields):
                votable_fields.append(column_name.lower())
        return votable_fields

class GaiaSearcher(Searcher):
    def __init__(self, search_parameters):
        """
        Initializes the GaiaSearcher class

        Parameters
        ----------
        search_parameters : dict
            The dictionary of search parameters
        """

        self.data_release = search_parameters.get("data_release", "DR3")
        self.data_release_keys = {"DR1": "gaiadr1", "DR2": "gaiadr2", "DR3": "gaiadr3"}
        data_release_epochs_dict = {"DR1": 2015.0, "DR2": 2015.5, "DR3": 2016.0}
        self.default_equinox = 2000.0
        self.default_epoch = data_release_epochs_dict[self.data_release]
        self.default_frame = "icrs"
        super().__init__(search_parameters=search_parameters)
        self.database_name = "Gaia"
        self.decimal_coordinate_keys = {"RA": "ra", "DEC": "dec"}
        self.source_name_key = {"Source Name": "DESIGNATION"}
        Gaia.MAIN_GAIA_TABLE = f"{self.data_release_keys[self.data_release]}.gaia_source"
        Gaia.ROW_LIMIT = search_parameters.get("row_limit", 100)

    def getQuery(self, fields=()):
        """
        Gets the query for the Gaia search with the given fields

        Parameters
        ----------
        fields : tuple
            The fields to include in the query

        Returns
        -------
        astropy.table.Table
            The table of the query results
        """

        # Construct the region query and get the result table
        result_table = None

        query_wcs = self.createWCS(self.search_coordinates)
        try:
            if (self.search_type == "Box" or self.search_type == "Box Search"):
                ax = plt.subplot(projection=query_wcs)
                ra_min, ra_max, dec_min, dec_max = self.getFOVLimits(self.search_coordinates, self.search_input, ax)
                plt.close()
                width = ra_max - ra_min
                height = dec_max - dec_min
                result_table = Gaia.query_object_async(coordinate=self.search_coordinates, width=width * u.deg, height=height * u.deg, columns=list(fields))
            elif (self.search_type == "Cone" or self.search_type == "Cone Search"):
                j = Gaia.cone_search_async(self.search_coordinates, radius=self.search_input, columns=list(fields))
                result_table = j.get_results()
        except Exception as e:
            raise QueryError(f"Query from {self.database_name} failed due to: {e}")

        if (result_table is None):
            self.result_table = Table()
            self.result_dataframe = pandas.DataFrame()
            return copy(self.result_table)

        result_table = self.fixTableUnits(result_table)

        self.result_table = copy(result_table)
        self.result_dataframe = copy(result_table.to_pandas())

        return self.result_table

    def getConditionalQuery(self, conditions=(), args=()):
        """
        Gets the query for the Gaia search with the given conditions

        Parameters
        ----------
        conditions : tuple
            The conditions to apply to the query
        args : tuple
            The arguments to apply to the query

        Returns
        -------
        astropy.table.Table
            The table of the query results with the given conditions
        """

        # Get the query
        self.getQuery(args)

        # Apply the conditions
        return self.applyQueryCriteria(conditions)
        
        

