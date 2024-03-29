import datetime
import functools
import inspect
import logging
import math
import os
import pickle
import time
import typing
import warnings
from copy import copy
from io import TextIOWrapper
from time import sleep

import astropy
import numpy as np
import pandas
import pandas as pd
import webbrowser
import matplotlib.pyplot as plt
import functools

import panoptes_client

from astropy.coordinates import SkyCoord
from astropy.table import Table
from tqdm import tqdm
from panoptes_client import Project, SubjectSet, Subject, User
from unWISE_verse.Spout import Spout, check_login
import astropy.units as u
from astropy.time import Time
from typing import List, Dict, Tuple, Union, Optional, TextIO, Any, Callable, Iterable, Set
from DataToolkit.Searcher import SimbadSearcher, GaiaSearcher
from DataToolkit.Plotter import SubjectCSVPlotter
from DataToolkit.Decorators import ignore_warnings, multioutput, plotting, timer
# TODO: Add a weighting system for users' classifications based on their accuracy on the known subjects
# TODO: Check hashed IP's to see if they're the same user as a logged-in user for classification counts, etc.
# TODO: Redo documentation for Analyzer class and add type hints, result hints, and docstrings for all methods
# TODO: Investigate Gini_coefficient for classification distribution analysis (As suggested by Marc)
from numpy import ndarray

file_typing = Union[str, TextIOWrapper, TextIO]

# Input typing for @uses_subject_ids decorator
subject_ids_input_typing = Union[str, int, TextIOWrapper, TextIO, pd.DataFrame, Iterable[Union[str, int]]]

# Input typing for @uses_user_identifiers decorator
user_identifiers_input_typing = Union[str, int, Iterable[Union[str, int]]]

astropy_quantity_input_typing = Union[int, float, u.Quantity]

# Processes subject_input into a list of integer subject_ids
def uses_subject_ids(func):
    """
    Decorator for Analyzer methods which converts the subject_input argument into an integer or a list of integers.

    Parameters
    ----------
    func : Callable
        The function to be decorated. Should be of the form func(self_or_cls, subject_input, *args, **kwargs) or func(subject_input, *args, **kwargs) .

    Returns
    -------
    Callable
        The decorated function with the subject_input argument converted.
    """

    @functools.wraps(func)
    def conversion_wrapper(*args, **kwargs):
        subject_ids = []
        is_static_method = isinstance(func, staticmethod)
        is_class_method = isinstance(func, classmethod)

        if(kwargs.get("subject_input") is not None):
            raise ValueError("Keyword argument 'subject_input' is not allowed. Let subject_input be the first positional argument instead.")

        if (func.__qualname__.find(".") == -1 or func.__qualname__.find("<locals>") != -1):
            self_or_cls = None
            subject_input = args[0]
            args = args[1:]
        else:
            self_or_cls = args[0]
            subject_input = args[1]
            args = args[2:]

        if (subject_input is None):
            warnings.warn(f"Could not find any input for the subject(s) to convert to a list of subject ids. Returning empty list.")

        elif ((isinstance(subject_input, str) and os.path.exists(subject_input)) or isinstance(subject_input, TextIOWrapper) or isinstance(subject_input, TextIO)):
            subject_ids = pd.read_csv(Analyzer.verifyFile(subject_input, ".csv"), usecols=["subject_id"])["subject_id"].tolist()
        elif(isinstance(subject_input, str) or isinstance(subject_input, int) or isinstance(subject_input, np.int64)):
            try:
                subject_ids = int(subject_input)
            except ValueError:
                raise ValueError("Invalid subject ID: " + str(subject_input))
        elif (isinstance(subject_input, pd.DataFrame)):
            subject_ids = subject_input["subject_id"].tolist()
        elif (isinstance(subject_input, Iterable)):
            for subject_id in subject_input:
                try:
                    subject_ids.append(int(subject_id))
                except ValueError:
                    raise ValueError("Invalid subject ID: " + str(subject_id))
            subject_input_type = type(subject_input)
            if (not isinstance(subject_input, list) and not isinstance(subject_input, tuple)):
                warnings.warn(f"Subject type argument {subject_input} of type '{subject_input_type.__name__}' is not a list or tuple. Returning results as a list.")
                subject_input_type = list
            subject_ids = subject_input_type(subject_ids)
        else:
            raise TypeError("Invalid subjects type: " + str(type(subject_input)))

        if(self_or_cls is None):
            if(is_static_method or is_class_method):
                return func.__func__(subject_ids, *args, **kwargs)
            else:
                return func(subject_ids, *args, **kwargs)
        else:
            return func(self_or_cls, subject_ids, *args, **kwargs)

    return conversion_wrapper

def uses_user_identifiers(func):
    """
    Decorator for Analyzer methods which converts the user_identifier argument into an integer (if possible) or a string or a list of integers or strings.

    Parameters
    ----------
    func : Callable
        The function to be decorated. Should be of the form func(self_or_cls, user_identifier, *args, **kwargs) or func(user_identifier, *args, **kwargs).

    Returns
    -------
    Callable
        The decorated function with the user_identifier argument converted.
    """

    @functools.wraps(func)
    def conversion_wrapper(*args, **kwargs):
        is_static_method = isinstance(func, staticmethod)
        is_class_method = isinstance(func, classmethod)
        if (func.__qualname__.find(".") == -1 or func.__qualname__.find("<locals>") != -1):
            self_or_cls = None
            user_identifier = args[0]
            args = args[1:]
        else:
            self_or_cls = args[0]
            user_identifier = args[1]
            args = args[2:]

        if(user_identifier is None):
            raise ValueError("No user identifier provided.")

        try:
            user_identifier = int(user_identifier)
        except ValueError:
            pass

        if(self_or_cls is None):
            if(is_static_method or is_class_method):
                return func.__func__(user_identifier, *args, **kwargs)
            else:
                return func(user_identifier, *args, **kwargs)
        else:
            return func(self_or_cls, user_identifier, *args, **kwargs)
    return conversion_wrapper


def days_since_launch(launch_day: datetime.date =datetime.date(2023, 6, 27), date: datetime.date = datetime.date.today()) -> int:
    """
    Returns the number of days since the launch day.

    Parameters
    ----------
    launch_day : datetime.date, optional
        The launch day of the project. Defaults to June 27, 2023.
    date : datetime.date, optional
        The date to calculate the number of days since the launch day. Defaults to today's date.

    Returns
    -------
    int
        The number of days since the launch day.
    """

    return (date - launch_day).days + 1

class Analyzer:
    def __init__(self, extracted_file: file_typing, reduced_file: file_typing, subjects_file: Optional[file_typing] = None) -> None:
        # Verify that the extracted_file is a valid file

        self.extracted_file, self.reduced_file = Analyzer.verifyFile((extracted_file, reduced_file), required_file_type=".csv")

        # Read the files as Pandas DataFrames
        self.extracted_dataframe = pd.read_csv(self.extracted_file)
        self.reduced_dataframe = pd.read_csv(self.reduced_file)

        self.subject_ids_dictionary = {}

        # Verify that the subjects_file is a valid file, if provided.
        if (subjects_file is not None):
            self.subjects_file = subjects_file
            self.subjects_dataframe = pd.read_csv(self.subjects_file)
            self.subject_ids_dictionary = {int(subject_id): int(subject_id) for subject_id in self.reduced_dataframe["subject_id"].values if subject_id in self.subjects_dataframe["subject_id"].values}
            print("You are using the offline Analyzer.")
        else:
            # If no subjects_file is provided, then the user must be logged in to access the subjects.
            self.subjects_file = None
            self.subjects_dataframe = None
            self.login()
            print("You are using the online Analyzer.")
            self.subject_dictionary = {}
            subject_ids = [int(subject_id) for subject_id in self.reduced_dataframe["subject_id"].values]
            with tqdm(total=len(subject_ids), desc="Loading subjects from Zooniverse", unit="subject") as progress_bar:
                for subject_id in subject_ids:
                    subject = Spout.get_subject(subject_id)
                    if(subject is not None):
                        self.subject_dictionary[subject_id] = subject
                        self.subject_ids_dictionary[subject_id] = subject_id
                    else:
                        warnings.warn(f"Could not find subject with ID {subject_id}.")
                    progress_bar.update(1)
            print(f"Finished loading {len(self.subject_dictionary)} subjects from Zooniverse.")

        self.classifier = Classifier(self)

        self.save()

    # Helper methods for Analyzer
    # ------------------------------------------------------------------------------------------------------------------

    def save(self, filename: str ='analyzer.pickle') -> None:
        """
        Save the Analyzer object as a pickle file.

        Parameters
        ----------
            filename : str
                A string representing the name of the file to save the Analyzer object as
                Defaults to 'analyzer.pickle'
        """

        if(self.subjects_file is None):
            raise ValueError("Cannot save online Analyzer.")

        with open(filename, 'wb') as file:
            pickle.dump(self, file)

        print(f"Saved Analyzer object as '{filename}'")

    @staticmethod
    def load(filename: str ='analyzer.pickle') -> 'Analyzer':
        """
        Load an Analyzer object from a pickle file.

        Parameters
        ----------
            filename : str
                A string representing the name of the file to load the Analyzer object from
                Defaults to 'analyzer.pickle'

        Returns
        -------
            analyzer : Analyzer
                The Analyzer object loaded from the pickle file
        """

        with open(filename, 'rb') as file:
            analyzer = pickle.load(file)
            print(f"Loaded Analyzer object from '{filename}'")
            return analyzer



    @staticmethod
    @multioutput
    def verifyFile(file: file_typing, required_file_type: Optional[str] = None) -> str:
        """
        Verifies that the file is a valid file.

        Parameters
        ----------
        file : Union[str, TextIOWrapper]
            The file to verify.
        required_file_type : Optional[str]
            The required file type of the file. If None, then the file type is not checked.

        Returns
        -------
        file_path : str
            The path to the file.

        Raises
        ------
        TypeError
            If the file is not a valid file input.
        FileNotFoundError
            If the file does not exist.
        ValueError
            If the file is not the required file type.

        """

        file_path = None

        if (isinstance(file, str)):
            file_path = file
        elif (isinstance(file, TextIOWrapper)):
            file_path = file.name
            file.close()
        else:
            raise TypeError(f"File '{file}' is not a valid file input.")

        if (not os.path.exists(file_path)):
            raise FileNotFoundError(f"File '{file_path}' does not exist.")

        if(required_file_type is not None):
            if(not file_path.endswith(required_file_type)):
                raise ValueError(f"File '{file_path}' is not a {required_file_type} file.")

        return file_path

    def login(self, save: bool = True) -> None:
        """
        Logs the user into the Zooniverse project and saves the login information to a file.

        Parameters
        ----------
        save : bool
            Whether to save the login information to a file.
        """

        # Get login
        login = Spout.requestLogin(save=save)

        # Login to Zooniverse via Spout
        Spout.loginToZooniverse(login)

    # Methods related to classifications
    # ------------------------------------------------------------------------------------------------------------------
    # All classifications methods
    def getTotalClassifications(self) -> int:
        """
        Returns the total number of classifications of valid subjects in the extracted file.

        Returns
        -------
        total_classifications : int
            The total number of classifications of valid subjects in the extracted file.
        """

        # Return the number of classifications in the extracted file
        subject_ids = self.getSubjectIDs()
        classification_dictionaries = self.getClassificationsForSubject(subject_ids, weighted=False)
        total_classifications = sum(classification_dictionary["total"] for classification_dictionary in classification_dictionaries)
        return total_classifications

    @plotting
    def plotClassificationDistribution(self, total_subject_count: Optional[int] = None, **kwargs) -> None:
        """
        Plots the classification distribution of the extracted file.

        Parameters
        ----------
        total_subject_count : int, optional
            The total number of subjects in the current workflow on Zooniverse.
            If None, then the total number of subjects is not used to find how many subjects have 0 classifications.

        **kwargs: optional
            Keyword arguments to pass to the matplotlib.pyplot.bar() function and the plotting decorator.
        """

        latest_datetime = datetime.datetime.strptime(self.extracted_dataframe["created_at"].max(), '%Y-%m-%d %H:%M:%S  UTC')
        # Set the default title
        plt.title(f"Classification Distribution: Day {days_since_launch(date=latest_datetime.date())}")

        # Set the default x and y labels
        plt.xlabel("Number of Classifications")
        plt.ylabel("Number of Subjects")

        number_of_classifications_dict = self.getCountDictionaryOfClassifications(total_subject_count)

        plt.bar(number_of_classifications_dict.keys(), number_of_classifications_dict.values(), **kwargs)

        for key in number_of_classifications_dict:
            plt.text(key, number_of_classifications_dict[key], number_of_classifications_dict[key], ha='center', va='bottom')

        plt.xticks(range(len(number_of_classifications_dict)))

        total_classifications = sum(key*value for key, value in number_of_classifications_dict.items())
        # Add a legend with the total number of classifications
        plt.legend([f"Total Classifications: {total_classifications}"])

    def computeTimeStatisticsForClassifications(self) -> Tuple[float, float, float]:
        """
        Computes the time statistics of the users' classifications.
        
        Returns
        -------
        users_average_time : float
            The average time between classifications for each user.
        users_std_time : float
            The standard deviation of the time between classifications for each user.
        users_median_time : float
            The median time between classifications for each user.
        """

        # Get the unique usernames
        user_identifiers = self.getUniqueUserIdentifiers(user_identifier="username")

        # Initialize the list of classification times
        users_classification_times = []

        # Iterate over all unique usernames
        for user_identifier in user_identifiers:

            # Get the user's classifications
            user_classifications = self.getClassificationsByUser(user_identifier)

            # Convert the created_at column to datetime objects
            user_times = pd.to_datetime(user_classifications["created_at"])

            # Initialize the previous index
            previous_index = None

            # Iterate over all indices in the user's classifications
            for index in user_times.index:
                # If there is a previous index, then compute the time difference
                if (previous_index is not None):
                    # Compute the time difference between the current and previous classification
                    time_difference = user_times[index] - user_times[previous_index]

                    # Set the upper time limit to 5 minutes
                    upper_time_limit = 60 * 5

                    # If the time difference is less than the upper time limit, then add it to the list of classification times
                    if (time_difference.seconds < upper_time_limit):
                        users_classification_times.append(time_difference.seconds)
                # Set the previous index to the current index
                previous_index = index

        # Compute the average time between classifications for all users
        users_average_time, users_std_time, users_median_time = self.computeTimeStatistics(users_classification_times)

        # Return the average time between classifications for all users
        return users_average_time, users_std_time, users_median_time

    def getCountDictionaryOfClassifications(self, total_subject_count: Optional[int] = None) -> Dict[int, int]:
        """
        Returns a dictionary containing how many subjects have a certain number of classifications. The keys are the
        number of classifications and the values are the number of subjects with that number of classifications.
        
        Parameters
        ----------
        total_subject_count : int, optional
            The total number of subjects in the current workflow on Zooniverse.
            If None, then the total number of subjects is not used to find how many subjects have 0 classifications.
        
        Returns
        -------
        number_of_classifications_dict : dict
            A dictionary containing how many subjects have a certain number of classifications.
        """
        
        subject_ids = self.getSubjectIDs()
        number_of_classifications_dict = {}

        for subject_id in subject_ids:
            classification_dict = self.getClassificationsForSubject(subject_id)

            total_classifications = classification_dict['yes'] + classification_dict['no']
            if (total_classifications in number_of_classifications_dict):
                number_of_classifications_dict[total_classifications] += 1
            else:
                number_of_classifications_dict[total_classifications] = 1

        counts = range(1, max(number_of_classifications_dict.keys())+1)

        for count in counts:
            if(count not in number_of_classifications_dict):
                number_of_classifications_dict[count] = 0

        if (total_subject_count is not None):
            number_of_classifications_dict[0] = total_subject_count - len(subject_ids)
        number_of_classifications_dict = dict(sorted(number_of_classifications_dict.items()))
        return number_of_classifications_dict

    # Subset of classifications method
    def getSubsetOfTotalClassifications(self, minimum_subject_classification_count: int = 0, total_subject_count: Optional[int] = None):
        """
        Returns the total number of classifications for subjects with at least the minimum number of classifications.
        
        Parameters
        ----------
        minimum_subject_classification_count : int, optional
            The minimum number of classifications a subject must have to be included in the total number of classifications.
            The default is 0.
        total_subject_count : int, optional
            The total number of subjects in the current workflow on Zooniverse.
            If None, then the total number of subjects is not used to find how many subjects have 0 classifications.
            
        Returns
        -------
        total_classifications : int
            The total number of classifications for subjects with at least the minimum number of classifications.
        """
        
        classification_count_dict = self.getCountDictionaryOfClassifications(total_subject_count)
        return sum(key*value for key, value in classification_count_dict.items() if key >= minimum_subject_classification_count)

    # User classification methods
    @uses_user_identifiers
    def getClassificationsByUser(self, user_identifier: user_identifiers_input_typing) -> pd.DataFrame:
        """
        Returns a dataframe containing all the classifications made by a user.

        Parameters
        ----------
        user_identifier : str, int, Iterable[str | int]
            The username or Zooniverse ID of the user.

        Returns
        -------
        user_classifications : pandas.DataFrame
            A dataframe containing all the classifications made by a user.
        """

        # Check if the user_id is a string or an integer
        if (isinstance(user_identifier, str)):
            # If it's a string, then it's a username
            username = user_identifier
            return self.extracted_dataframe[self.extracted_dataframe["user_name"] == username]
        else:
            # If it's an integer, then it's a Zooniverse ID
            user_id = user_identifier
            return self.extracted_dataframe[self.extracted_dataframe["user_id"] == user_id]

    @multioutput
    @uses_user_identifiers
    def getTotalClassificationsByUser(self, user_identifier: user_identifiers_input_typing) -> int:
        """
        Returns the number of classifications made by a user.

        Parameters
        ----------
        user_identifier : str, int, Iterable[str | int]
            The username or Zooniverse ID of the user.

        Returns
        -------
        user_classification_count : int
            The number of classifications made by a user.
        """

        # Return the number of classifications made by that user
        return len(self.getClassificationsByUser(user_identifier))

    # Top users classification methods
    def getTotalClassificationsByTopUsers(self, classification_threshold: Optional[int] = None, percentile : Optional[float] = None) -> int:
        """
        Returns the total number of classifications made by the top users.

        Parameters
        ----------
        classification_threshold : int, optional
            The minimum number of classifications a user must have to be included in the total number of classifications.
            The default is None, which means that the top users are not filtered by the number of classifications.

        percentile : float, optional
            The percentile of users to include in the total number of classifications.
            Should be a float between 0 and 100.
            The default is None, which means that the top users are not filtered by percentile.

        Returns
        -------
        total_classifications : int
            The total number of classifications made by the top users.
        """

        top_users_dict = self.getClassificationDictionaryOfTopUsers(classification_threshold=classification_threshold, percentile=percentile)
        return sum(top_users_dict.values())

    @plotting
    def plotTotalClassificationsByTopUsers(self, classification_threshold: Optional[int] = None, percentile: Optional[float] = None, **kwargs) -> None:
        """
        Plots the total number of classifications made by the top users.

        Parameters
        ----------
        classification_threshold : int, optional
            The minimum number of classifications a user must have to be included in the total number of classifications.
            The default is None, which means that the top users are not filtered by the number of classifications.

        percentile : float, optional
            The percentile of users to include in the total number of classifications.
            Should be a float between 0 and 100.
            The default is None, which means that the top users are not filtered by percentile.

        **kwargs : optional
            Keyword arguments to pass to the matplotlib.pyplot.bar() function and the plotting decorator.
        """

        top_users_dict = self.getClassificationDictionaryOfTopUsers(classification_threshold=classification_threshold, percentile=percentile)
        # Plot the number of classifications made by each user but stop names from overlapping
        usernames = list(top_users_dict.keys())
        user_counts = list(top_users_dict.values())
        # Generate x-coordinates for the bars
        x = np.arange(len(top_users_dict))

        # Create the bar plot
        ax = plt.gca()

        # Set the default title
        if (percentile is not None):
            plt.title(f"Users in the Top {100 - percentile}% of Classifications")
        elif (classification_threshold is not None):
            plt.title(f"Users with More Than {classification_threshold} Classifications")

        # Set the default y label
        plt.ylabel("Number of Classifications", fontsize=15)

        anonymous = kwargs.pop("anonymous", False)

        if (not anonymous):
            ax.set_xticks(x)
            ax.set_xticklabels(usernames, ha='right', va='top', rotation=45, color="black")
        else:
            ax.set_xticks([])
            ax.set_xticklabels([])

        bars = ax.bar(x, user_counts, **kwargs)

        offset = 10
        user_counts_fontsize = 7
        for i, bar in enumerate(bars):
            # Display the user's count
            ax.text(bar.get_x() + bar.get_width() / 2, user_counts[i] + offset, str(user_counts[i]), horizontalalignment='center', verticalalignment='bottom', fontsize=user_counts_fontsize)

        # Add a legend which provides the total number of classifications of all the top users
        total_classification_count = self.getTotalClassifications()
        top_users_total = sum(user_counts)
        ax.legend([f"Total: {top_users_total}, Contribution Percentage: {round((top_users_total/total_classification_count) * 100, 2)}%"], loc='upper right')

    # Subject classification methods
    @multioutput
    @uses_subject_ids
    def getClassificationsForSubject(self, subject_input: subject_ids_input_typing, weighted: bool = False) -> Dict[str, int]:
        """
        Returns the classifications made for a subject as a classification dictionary.

        Parameters
        ----------
        subject_input :  str | int | TextIOWrapper | TextIO | DataFrame | Iterable[str | int]
            The subject ID of the subject.

        weighted : bool, optional
            Whether to return the weighted classification dictionary or not.
            Weights are calculated by each user's accuracy.
            The default is False.

        Returns
        -------
        subject_classifications : Dict[str, int]
            A classification dictionary containing the classifications made for the subject.
            Consists of the keys "yes", "no", and "total".
        """

        if(not self.subjectExists(subject_input)):
            warnings.warn(f"Subject {subject_input} does not exist, returning None.")
            return None

        if(not weighted):
            subject_dataframe = self.getSubjectDataframe(subject_input, dataframe_type="reduced")

            if (len(subject_dataframe) == 0):
                warnings.warn(f"Subject {subject_input} is not in the reduced file, returning None.")
                return None

            try:
                # Try to get the number of "yes" classifications
                yes_count = int(subject_dataframe["data.yes"].values[0])
            except ValueError:
                # If there are no "yes" classifications, then set the count to 0
                yes_count = 0

            try:
                # Try to get the number of "no" classifications
                no_count = int(subject_dataframe["data.no"].values[0])
            except ValueError:
                # If there are no "no" classifications, then set the count to 0
                no_count = 0

            classification_dict = {"yes": yes_count, "no": no_count, "total": yes_count + no_count}
            return classification_dict
        else:
            subject_dataframe = self.getSubjectDataframe(subject_input, dataframe_type="extracted")

            if (len(subject_dataframe) == 0):
                warnings.warn(f"Subject {subject_input} is not in the extracted file, returning None.")
                return None

            yes_count = 0
            no_count = 0
            for index, row in subject_dataframe.iterrows():
                default_insufficient_classifications = True
                try:
                    # Try to get the number of "yes" classifications
                    yes_count += int(row["data.yes"]) * self.classifier.getUserAccuracy(row["user_name"], default_insufficient_classifications=default_insufficient_classifications)
                except:
                    # If there are no "yes" classifications, then set the count to 0
                    yes_count += 0


                try:
                    # Try to get the number of "no" classifications
                    no_count += int(row["data.no"]) * self.classifier.getUserAccuracy(row["user_name"], default_insufficient_classifications=default_insufficient_classifications)
                except:
                    # If there are no "no" classifications, then set the count to 0
                    no_count += 0

        # Return the dictionary of the number of "yes" and "no" classifications
        classification_dict = {"yes": yes_count, "no": no_count, "total": yes_count + no_count}
        return classification_dict

    @multioutput
    @plotting
    @uses_subject_ids
    def plotClassificationsForSubject(self, subject_input: subject_ids_input_typing, **kwargs) -> None:
        """
        Plots the classifications made for a subject as a pie chart.

        Parameters
        ----------
        subject_input :  str | int | TextIOWrapper | TextIO | DataFrame | Iterable[str | int]
            The subject ID of the subject.

        **kwargs, optional
            Additional keyword arguments to pass to the matplotlib.plt.pie() function or the plotting decorator.
        """

        # Get the number of "yes" and "no" classifications for that subject as a dictionary
        classification_dict = self.getClassificationsForSubject(subject_input)
        # Get the number of "yes" and "no" classifications from the dictionary
        yes_count = classification_dict["yes"]
        no_count = classification_dict["no"]

        # Compute the total number of classifications
        total_count = yes_count + no_count

        # Compute the percentage of "yes" and "no" classifications
        yes_percent = yes_count / total_count
        no_percent = no_count / total_count

        labels = kwargs.pop("labels", ["Yes", "No"])
        autopct = kwargs.pop("autopct", '%1.1f%%')
        loc = kwargs.pop("loc", "upper left")

        # Plot the pie chart
        plt.pie([yes_percent, no_percent], labels=labels, autopct=autopct, **kwargs)
        plt.axis('equal')

        # Set the title
        plt.title("Subject ID: " + str(subject_input) + " Classifications")
        plt.legend([f"{yes_count} Yes classifications", f"{no_count} No classifications"], loc=loc)

    # Classification time statistics methods
    @multioutput
    @uses_user_identifiers
    def getClassificationTimesByUser(self, user_identifier: user_identifiers_input_typing) -> List[float]:
        """
        Returns the valid time differences between each classification made by a user.
        There are exceptions for insufficient consecutive classifications and time differences over 5 minutes are excluded.

        Parameters
        ----------
        user_identifier : str, int, Iterable[str | int]
            The user ID of the user.

        Returns
        -------
        user_classification_times : List[float]
            A list of the valid time differences between each classification made by the user.
        """

        # Get the user's classifications
        user_classifications = self.getClassificationsByUser(user_identifier)
        user_classification_times = []

        # Convert the created_at column to datetime objects
        user_times = pd.to_datetime(user_classifications["created_at"])

        # Initialize the previous index
        previous_index = None

        # Iterate over all indices in the user's classifications
        for index in user_times.index:
            # If there is a previous index, then compute the time difference
            if (previous_index is not None):
                # Compute the time difference between the current and previous classification
                time_difference = user_times[index] - user_times[previous_index]

                # Set the upper time limit to 5 minutes
                upper_time_limit = 60 * 5

                # If the time difference is less than the upper time limit, then add it to the list of classification times
                if (time_difference.seconds < upper_time_limit):
                    user_classification_times.append(time_difference.seconds)
            # Set the previous index to the current index
            previous_index = index

        if(len(user_classification_times) == 0 and len(user_times) > 0):
            warnings.warn(f"User with user_identifier '{user_identifier}' has too few consecutive classifications, returning an empty list.")
        elif(len(user_classification_times) == 0 and len(user_times) == 0):
            warnings.warn(f"User with user_identifier '{user_identifier}' has no classifications, returning an empty list.")

        return user_classification_times

    @multioutput
    @uses_user_identifiers
    def computeTimeStatisticsForUser(self, user_identifier: user_identifiers_input_typing) -> Tuple[float, float, float]:
        """
        Computes the average, standard deviation, and median of the classification times for a user.

        Parameters
        ----------
        user_identifier : str, int, Iterable[str | int]
            The user ID of the user.

        Returns
        -------
        user_average_time : float
            The average time between classifications for the user.
        user_std_time : float
            The standard deviation of the time between classifications for the user.
        user_median_time : float
            The median of the time between classifications for the user.
        """

        # Get the user's classification times
        user_classification_times = self.getClassificationTimesByUser(user_identifier)

        if (len(user_classification_times) == 0):
            raise ValueError(f"User with user_identifier {user_identifier} has no classifications or does not exist.")

        # Compute the average, standard deviation, and median of the user's classification times
        user_average_time, user_std_time, user_median_time = self.computeTimeStatistics(user_classification_times)

        # Return the average time between classifications for all users
        return user_average_time, user_std_time, user_median_time

    @staticmethod
    def computeTimeStatistics(classification_times: List[float]) -> Tuple[float, float, float]:
        """
        Computes the average, standard deviation, and median of the classification times from a list of classification times.

        Parameters
        ----------
        classification_times : List[float]
            A list of the classification times.

        Returns
        -------
        average_time : float
            The average time between classifications.
        std_time : float
            The standard deviation of the time between classifications.
        median_time : float
            The median of the time between classifications.
        """

        if(len(classification_times) == 0):
            raise ValueError(f"Classification times list is empty.")

        average_time = float(sum(classification_times) / len(classification_times))

        std_time = float(np.std(classification_times))

        median_time = float(np.median(classification_times))

        # Return the average time between classifications for a list of user(s) classification times
        return average_time, std_time, median_time

    @plotting
    def plotTimeHistogramForAllClassifications(self, **kwargs) -> None:
        """
        Plots a histogram of the time between classifications for all users.

        Parameters
        ----------
        kwargs, optional
            Additional keyword arguments to pass to the matplotlib.pyplot.hist function and the plotting decorator.
        """

        # Get the number of bins and range from the kwargs
        bins = kwargs.pop("bins", 100)
        hist_range = kwargs.pop("range", None)

        # Get the unique usernames
        user_identifiers = self.getUniqueUserIdentifiers(user_identifier="username")

        all_classification_times = []

        # Iterate over all usernames
        for user_identifier in user_identifiers:
            # Get the user's classification times
            user_classification_times = self.getClassificationTimesByUser(user_identifier)

            # Convert the list of lists to a single list
            all_classification_times.extend(user_classification_times)

        # Compute the classification time statistics for all users
        all_average_time, all_std_time, all_median_time = self.computeTimeStatistics(all_classification_times)

        if (hist_range is None):
            hist_range = min(min(all_classification_times), all_average_time - all_std_time), max(max(all_classification_times), all_average_time + all_std_time)

        # Plot the histogram
        plt.hist(all_classification_times, bins=bins, range=hist_range, **kwargs)

        plt.axvline(all_average_time, color='red', linestyle='solid', linewidth=1, label=f"Average: {round(all_average_time, 2)} seconds")

        plt.axvline(all_average_time + all_std_time, color='red', linestyle='dashed', linewidth=1, label=f"Average ± Standard Deviation: {round(all_average_time, 2)} ± {round(all_std_time, 2)} seconds")
        plt.axvline(all_average_time - all_std_time, color='red', linestyle='dashed', linewidth=1)

        plt.axvline(all_median_time, color='orange', linestyle='solid', linewidth=1, label=f"Median: {round(all_median_time, 2)} seconds")
        plt.title("Classification Time Histogram")
        plt.xlabel("Time (seconds)")
        plt.ylabel("Counts")
        plt.legend()

    @multioutput
    @plotting
    @uses_user_identifiers
    def plotTimeHistogramForUserClassifications(self, user_identifier: user_identifiers_input_typing, **kwargs) -> None:
        """
        Plots a histogram of the time between classifications for a user.

        Parameters
        ----------
        user_identifier : str, int, Iterable[str | int]
            The user ID of the user.
        kwargs, optional
            Additional keyword arguments to pass to the matplotlib.pyplot.hist function and the plotting decorator.
        """

        bins = kwargs.pop("bins", 100)
        hist_range = kwargs.pop("range", None)

        # Initialize the list of classification times
        user_classification_times = self.getClassificationTimesByUser(user_identifier)

        if(len(user_classification_times) == 0):
            warnings.warn(f"User with user_identifier '{user_identifier}' has no classification times, cannot plot their classification time histogram.")
            return None

        # Compute the classification time statistics for the user
        user_average_time, user_std_time, user_median_time = self.computeTimeStatistics(user_classification_times)

        if (hist_range is None):
            hist_range = min(min(user_classification_times), user_average_time - user_std_time), max(max(user_classification_times), user_average_time + user_std_time)

        # Plot the histogram
        plt.hist(user_classification_times, bins=bins, range=hist_range, **kwargs)

        plt.axvline(user_average_time, color='red', linestyle='solid', linewidth=1, label=f"Average: {round(user_average_time, 2)} seconds")

        plt.axvline(user_average_time + user_std_time, color='red', linestyle='dashed', linewidth=1, label=f"Average ± Standard Deviation: {round(user_average_time, 2)} ± {round(user_std_time, 2)} seconds")
        plt.axvline(user_average_time - user_std_time, color='red', linestyle='dashed', linewidth=1)

        plt.axvline(user_median_time, color='orange', linestyle='solid', linewidth=1, label=f"Median: {round(user_median_time, 2)} seconds")

        plt.title(f"{user_identifier} Classification Time Histogram")
        plt.xlabel("Time (seconds)")
        plt.ylabel("Counts")
        plt.legend()

    @plotting
    def plotClassificationTimeline(self, bar: bool = True, binning_parameter: str = "Day", label: bool = True, **kwargs) -> None:
        """
        Plots a timeline of the classifications.

        Parameters
        ----------
        bar : bool, optional
            Whether to plot the timeline as a bar graph or a line graph.
        binning_parameter : str, optional
            The parameter to bin the classifications by. Can be "Day", "Week", "Month", or "Year".
        label : bool, optional
            Whether to label the values on the plot.
        """

        # Get the classification datetimes
        classification_datetimes = pd.to_datetime(self.extracted_dataframe["created_at"])

        # Initialize the binned datetimes dictionary
        binned_datetimes = {}

        # Iterate over all classification datetimes
        for classification_datetime in classification_datetimes:

            # Bin the datetimes
            if (binning_parameter == "Day"):
                day = classification_datetime.date()
                if day in binned_datetimes:
                    binned_datetimes[day].append(classification_datetime)
                else:
                    binned_datetimes[day] = [classification_datetime]
            elif (binning_parameter == "Week"):
                week = classification_datetime.isocalendar()[1]
                if week in binned_datetimes:
                    binned_datetimes[week].append(classification_datetime)
                else:
                    binned_datetimes[week] = [classification_datetime]
            elif (binning_parameter == "Month"):
                month = classification_datetime.month
                if month in binned_datetimes:
                    binned_datetimes[month].append(classification_datetime)
                else:
                    binned_datetimes[month] = [classification_datetime]
            elif (binning_parameter == "Year"):
                year = classification_datetime.year
                if year in binned_datetimes:
                    binned_datetimes[year].append(classification_datetime)
                else:
                    binned_datetimes[year] = [classification_datetime]

        # Convert the binned datetimes to a dictionary of counts
        binned_datetimes = {k: len(v) for k, v in binned_datetimes.items()}

        # Plot the timeline
        if (label):
            for key, value in binned_datetimes.items():
                plt.annotate(str(value), xy=(key, value), ha='center', va='bottom')

        if (bar):
            plt.bar(binned_datetimes.keys(), binned_datetimes.values(), **kwargs)
        else:
            plt.plot(binned_datetimes.keys(), binned_datetimes.values(), **kwargs)
        plt.title("Classification Timeline")
        plt.xlabel(binning_parameter)
        plt.ylabel("Classifications")

    # Methods related to users
    # ------------------------------------------------------------------------------------------------------------------
    # Principle method for getting users
    def getUniqueUserIdentifiers(self, include_logged_out_users: bool = True, user_identifier: str = "username") -> Union[List[str], List[int]]:
        """
        Gets the unique user identifiers for the classifications.

        Parameters
        ----------
        include_logged_out_users : bool, optional
            Whether to include users who were logged out when they made their classification(s).
        user_identifier : str, optional
            The identifier to use for the user. Can be "username" or "user id".

        Returns
        -------
        List[str | int]
            The unique user identifiers.

        Raises
        ------
        ValueError
            If user_identifier is not "username" or "user id".

        ValueError
            If include_logged_out_users is True and user_identifier is "user id".

        Notes
        -----
        I tried doing a cross-match between ip addresses and users, but there were no logged-out users
        which had the same ip address as a logged-in user. So I don't think its worth the effort to implement.
        """

        user_dataframe_key = None
        if(user_identifier == "username"):
            user_dataframe_key = "user_name"
        elif(user_identifier == "user id"):
            user_dataframe_key = "user_id"
        else:
            raise ValueError("user_identifier must be either 'username' or 'user id'.")

        if(include_logged_out_users and user_identifier == "user id"):
            raise ValueError("Cannot include logged out users when user_identifier is 'user id'.")

        if(include_logged_out_users):
            return list(self.extracted_dataframe[user_dataframe_key].unique())
        else:
            unique_users = list(self.extracted_dataframe[user_dataframe_key].unique())
            if(user_identifier == "username"):
                logged_in_unique_users = [username for username in unique_users if "not-logged-in" not in username]
            else:
                logged_in_unique_users = [user_id for user_id in unique_users if not np.isnan(user_id)]
            return logged_in_unique_users

    # Spout-interface method
    @multioutput
    @uses_user_identifiers
    def getUser(self, user_identifier: user_identifiers_input_typing) -> Union[User, List[User]]:
        """
        Gets the user(s) from the Spout database.

        Parameters
        ----------
        user_identifier : str | int | List[str | int]
            The user identifier(s) to get the user(s) for.

        Returns
        -------
        User | List[User]
            The user(s).
        """

        return Spout.get_user(user_identifier)

    # Methods related to top users
    def getClassificationDictionaryOfTopUsers(self, classification_threshold: int = None, percentile: float = None) -> Dict[str, int]:
        """
        Gets a dictionary of the top users and their number of classifications.

        Parameters
        ----------
        classification_threshold : int, optional
            The minimum number of classifications a user must have to be considered a top user.
        percentile : float, optional
            The percentile of users to consider as top users.

        Returns
        -------
        Dict[str, int]
            A dictionary of the top users and their number of classifications.
        """

        if (classification_threshold is None and percentile is None):
            raise ValueError("You must provide either a classification_threshold or a percentile.")
        elif(classification_threshold is not None and percentile is not None):
            raise ValueError("You cannot provide both a classification_threshold and a percentile.")

        unique_user_identifiers = self.getUniqueUserIdentifiers(user_identifier="username")
        user_classifications_dict = {}

        for unique_user_identifier in unique_user_identifiers:
            user_classifications_dict[unique_user_identifier] = self.getTotalClassificationsByUser(unique_user_identifier)

        # Sort the dictionary by the number of classifications
        sorted_user_classifications_dict = {k: v for k, v in sorted(user_classifications_dict.items(), key=lambda item: item[1])}

        # Reverse the dictionary
        sorted_user_classifications_dict = dict(reversed(list(sorted_user_classifications_dict.items())))

        top_users_dict = {}

        def userMeetsRequirements(user, classification_threshold, percentile):
            if(percentile is not None):
                if(sorted_user_classifications_dict[user] >= np.percentile(list(sorted_user_classifications_dict.values()), percentile)):
                    return True
                else:
                    return False
            else:
                if(sorted_user_classifications_dict[user] >= classification_threshold):
                    return True
                else:
                    return False

        for user in sorted_user_classifications_dict:
            if(userMeetsRequirements(user, classification_threshold, percentile)):
                top_users_dict[user] = sorted_user_classifications_dict[user]

        return top_users_dict

    def getTopUsernames(self, classification_threshold: int = None, percentile: float = None) -> List[str]:
        """
        Gets a list of the top usernames.

        Parameters
        ----------
        classification_threshold : int, optional
            The minimum number of classifications a user must have to be considered a top user.
        percentile : float, optional
            The percentile of users to consider as top users.

        Returns
        -------
        List[str]
            A list of the top usernames.
        """

        top_usernames_dict = self.getClassificationDictionaryOfTopUsers(classification_threshold=classification_threshold, percentile=percentile)
        return list(top_usernames_dict.keys())

    def getTopUsernamesCount(self, classification_threshold: int = None, percentile: float= None) -> int:
        """
        Gets the number of top usernames.

        Parameters
        ----------
        classification_threshold : int, optional
            The minimum number of classifications a user must have to be considered a top user.
        percentile : float, optional
            The percentile of users to consider as top users.

        Returns
        -------
        int
            The number of top usernames.
        """

        top_users_dict = self.getClassificationDictionaryOfTopUsers(classification_threshold=classification_threshold,percentile=percentile)
        return len(top_users_dict)

    def getTopUsers(self, classification_threshold: int = None, percentile: float = None) -> List[User]:
        """
        Gets the top users.

        Parameters
        ----------
        classification_threshold : int, optional
            The minimum number of classifications a user must have to be considered a top user.
        percentile : float, optional
            The percentile of users to consider as top users.

        Returns
        -------
        List[User]
            The top users.
        """

        top_usernames = self.getTopUsernames(classification_threshold=classification_threshold, percentile=percentile)
        return self.getUser(top_usernames)

    # Methods related to subjects and subject metadata
    # ------------------------------------------------------------------------------------------------------------------
    # Principle method for getting subjects
    def getSubjectIDs(self) -> List[int]:
        """
        Gets the valid subject IDs associated with the classifications.

        Returns
        -------
        List[int]
            The valid subject IDs associated with the classifications.
        """

        # Return the list of subject IDs
        return list(self.subject_ids_dictionary.values())

    # Spout-interface method
    @multioutput
    @uses_subject_ids
    def getSubject(self, subject_input: subject_ids_input_typing) -> Union[Subject, List[Subject]]:
        """
        Gets the subject(s) from Zooniverse, using Spout.

        Parameters
        ----------
        subject_input :  str | int | TextIOWrapper | TextIO | DataFrame | Iterable[str | int]
            The subject ID(s) to get the subject(s) for.

        Returns
        -------
        Subject | List[Subject]
            The subject(s).
        """

        # Get the subject with the given subject ID in the subject set with the given subject set ID
        if(self.subjects_file is not None):
            return Spout.get_subject(subject_input)

        subject = self.subject_dictionary.get(subject_input, None)
        return subject


    # Subject dataframe methods
    @multioutput
    @uses_subject_ids
    def getSubjectDataframe(self, subject_input: subject_ids_input_typing, dataframe_type: str = "default") -> Union[pd.DataFrame, List[pd.DataFrame]]:
        """
        Gets the subject(s) as a dataframe(s).

        Parameters
        ----------
        subject_input :  str | int | TextIOWrapper | TextIO | DataFrame | Iterable[str | int]
            The subject ID(s) to get the subject(s) for.

        Returns
        -------
        pd.DataFrame | List[pd.DataFrame]
            The subject(s) as a dataframe(s).
        """

        @multioutput
        @uses_subject_ids
        def getSubjectDataframeFromID(subject_input: subject_ids_input_typing, dataframe_type: str = "default") -> Union[pd.DataFrame, List[pd.DataFrame]]:

            if(not isinstance(subject_input, int)):
                raise ValueError("The subject_id must be an integer.")

            if(not self.subjectExists(subject_input)):
                warnings.warn(f"Subject {subject_input} does not exist, returning empty Dataframe.")
                return pd.DataFrame()

            if (dataframe_type == "default"):
                # If it is default, then return the metadata of the subject as a dataframe
                subject_metadata = self.getSubjectMetadata(subject_input)

                if(subject_metadata is None):
                    warnings.warn(f"Subject {subject_input} does not exist, returning empty Dataframe.")
                    return pd.DataFrame()

                # Add the standard subject_id column
                subject_metadata["subject_id"] = subject_input

                # Add the standard metadata column
                subject_metadata["metadata"] = str(subject_metadata)

                # Convert the metadata to a dataframe
                subject_metadata_dataframe = pd.DataFrame.from_dict(subject_metadata, orient="index").transpose()
                return subject_metadata_dataframe

            elif (dataframe_type == "reduced"):
                # If reduced, then return the reduced dataframe for that subject
                return self.reduced_dataframe[self.reduced_dataframe["subject_id"] == subject_input]
            elif (dataframe_type == "extracted"):
                # If not reduced, then return the extracted dataframe for that subject
                return self.extracted_dataframe[self.extracted_dataframe["subject_id"] == subject_input]

        return getSubjectDataframeFromID(subject_input, dataframe_type=dataframe_type)

    @staticmethod
    def combineSubjectDataframes(subject_dataframes: Iterable[pd.DataFrame]) -> pd.DataFrame:
        """
        Combines a list of subject dataframes into a single dataframe.

        Parameters
        ----------
        subject_dataframes : Iterable[pd.DataFrame]
            The list of subject dataframes to combine.

        Returns
        -------
        pd.DataFrame
            The combined subject dataframe.

        Notes
        -----
        Dataframes which have the same subject_id will be combined into a single row to avoid duplication.
        """

        # Combine a list of subject dataframes into a single dataframe
        if(not isinstance(subject_dataframes, Iterable)):
            if(isinstance(subject_dataframes, pd.DataFrame)):
                return subject_dataframes
            else:
                raise ValueError("The subject_dataframes must be a list of dataframes.")

        subject_dataframe = pd.concat(subject_dataframes, ignore_index=True)
        subject_dataframe.drop_duplicates(subset=["subject_id"], inplace=True)
        subject_dataframe.reset_index(drop=True, inplace=True)
        return subject_dataframe

    # Subject metadata methods
    @multioutput
    @uses_subject_ids
    def subjectExists(self, subject_input: subject_ids_input_typing) -> Union[bool, List[bool]]:
        """
        Checks if the subject exists.

        Parameters
        ----------
        subject_input :  str | int | TextIOWrapper | TextIO | DataFrame | Iterable[str | int]
            The subject ID(s) to check.

        Returns
        -------
        bool | List[bool]
            Whether the subject exists.

        Notes
        -----
        This method is used to check if a subject exists before doing operations with a subject. Upon initialization of the
        Analyzer, the subject IDs are checked and if they do not exist, they are not added to the subject IDs dictionary.
        This is done to ensure that the subject IDs are valid and that the subject exists but in a time-efficient manner.
        """

        return subject_input in self.subject_ids_dictionary

    @multioutput
    @uses_subject_ids
    def getSubjectMetadata(self, subject_input: subject_ids_input_typing) -> Union[dict, List[dict]]:
        """
        Gets the subject metadata for the given subject ID.

        Parameters
        ----------
        subject_input :  str | int | TextIOWrapper | TextIO | DataFrame | Iterable[str | int]
            The subject ID(s) to get the subject metadata for.

        Returns
        -------
        dict | List[dict]
            The subject metadata.

        Notes
        -----
        This method is used to get the subject metadata for the given subject ID. If the subject ID does not exist, then
        a warning is raised and None is returned.
        """

        if(not self.subjectExists(subject_input)):
            warnings.warn(f"Subject ID {subject_input} was not found: Returning None")
            return None

        # Get the subject with the given subject ID
        if(self.subjects_file is not None):
            subject_dataframe = self.subjects_dataframe[self.subjects_dataframe["subject_id"] == subject_input]
            if(subject_dataframe.empty):
                warnings.warn(f"Subject ID {subject_input} was not found in subjects file, {self.subjects_file}: Returning None")
                return None
            else:
                return eval(self.subjects_dataframe[self.subjects_dataframe["subject_id"] == subject_input].iloc[0]["metadata"])
        else:
            subject = self.getSubject(subject_input)

            try:
                return subject.metadata
            except AttributeError:
                warnings.warn(f"Subject ID {subject_input} was not found: Returning None")
                return None

    @multioutput
    @uses_subject_ids
    def getSubjectMetadataField(self, subject_input: subject_ids_input_typing, field_name: str) -> Union[any, List[any]]:
        """
        Gets the subject metadata field for the given subject ID.

        Parameters
        ----------
        subject_input :  str | int | TextIOWrapper | TextIO | DataFrame | Iterable[str | int]
            The subject ID(s) to get the subject metadata field for.
        field_name : str
            The name of the field to get the value for.

        Returns
        -------
        any | List[any]
            The subject metadata field value.
        """

        # Get the subject metadata for the subject with the given subject ID

        subject_metadata = self.getSubjectMetadata(subject_input)

        if(subject_metadata is None):
            warnings.warn(f"Subject ID {subject_input} was not found: Returning None")
            return None

        # Get the metadata field with the given field name
        field_value = subject_metadata.get(field_name, None)

        if (field_value is None):
            warnings.warn(f"Field name {field_name} was not found in subject ID {subject_input}'s metadata: Returning None")
            return None
        else:
            return field_value

    # Subject WiseView image method
    @multioutput
    @uses_subject_ids
    def showSubjectInWiseView(self, subject_input: subject_ids_input_typing, open_in_browser: bool = False) -> Union[str, List[str]]:
        """
        Shows the subject in WiseView.

        Parameters
        ----------
        subject_input :  str | int | TextIOWrapper | TextIO | DataFrame | Iterable[str | int]
            The subject ID(s) to show in WiseView.
        open_in_browser : bool, optional
            Whether to open the subject in the default web browser, by default False

        Returns
        -------
        str | List[str]
            The WiseView link(s) for the subject(s).

        Notes
        -----
        This method is used to show the subject in WiseView. If the subject ID does not exist, then a warning is raised
        and None is returned. If the subject does not have a WiseView link, then a warning is raised and None will be returned.

        If open_in_browser is True, then the subject will be opened in the default web browser. However, to avoid accidentally
        opening too many subjects at once, there is a delay of 10 seconds between each subject being opened.
        """

        # Get the WiseView link for the subject with the given subject ID
        wise_view_link = self.getSubjectMetadataField(subject_input, "WISEVIEW")

        if wise_view_link is None:
            warnings.warn(f"No WiseView link found for subject ID {subject_input}: Returning None")
            return None

        # Remove the WiseView link prefix and suffix
        wise_view_link = wise_view_link.removeprefix("[WiseView](+tab+")
        wise_view_link = wise_view_link.removesuffix(")")

        # Determine whether to open the subject in the default web browser

        if (open_in_browser):
            webbrowser.open(wise_view_link)
            delay = 10
            print(f"Opening WiseView link for subject ID {subject_input}.")
            sleep(delay)

        # Return the WiseView link
        return wise_view_link

    # Subject SIMBAD link method
    @multioutput
    @uses_subject_ids
    def getSimbadLinkForSubject(self, subject_input: subject_ids_input_typing) -> Union[str, List[str]]:
        """
        Gets the SIMBAD link for the subject.

        Parameters
        ----------
        subject_input :  str | int | TextIOWrapper | TextIO | DataFrame | Iterable[str | int]
            The subject ID(s) to get the SIMBAD link for.

        Returns
        -------
        str | List[str]
            The SIMBAD link(s) for the subject(s).
        """

        simbad_link = self.getSubjectMetadataField(subject_input, "SIMBAD")

        if (simbad_link is None):
            warnings.warn(f"No SIMBAD link found for subject ID {subject_input}: Returning None")
            return None

            # Remove the SIMBAD link prefix and suffix
        simbad_link = simbad_link.removeprefix("[SIMBAD](+tab+")
        simbad_link = simbad_link.removesuffix(")")

        return simbad_link

    # Methods related to database queries
    # ------------------------------------------------------------------------------------------------------------------
    # SIMBAD query methods
    @multioutput
    @uses_subject_ids
    def getSimbadQueryForSubject(self, subject_input: subject_ids_input_typing, search_type: str = "Box Search", FOV: astropy_quantity_input_typing = 120*u.arcsec, radius: astropy_quantity_input_typing = 60*u.arcsec, plot: bool = False, separation: astropy_quantity_input_typing = None) -> Union[Table, List[Table]]:
        """
        Gets the SIMBAD query for the subject.

        Parameters
        ----------

        subject_input :  str | int | TextIOWrapper | TextIO | DataFrame | Iterable[str | int]
            The subject ID(s) to get the SIMBAD query for.
        search_type : str, optional
            The type of SIMBAD search to perform, by default "Box Search". The options are "Box Search" and "Cone Search".
        FOV : int | float | u.Quantity, optional
            The field of view to search in, by default 120*u.arcsec. If a number is given, then it is assumed to be in arcseconds.
        radius : int | float | u.Quantity, optional
            The radius to search in, by default 60*u.arcsec. If a number is given, then it is assumed to be in arcseconds.
        plot : bool, optional
            Whether to plot the SIMBAD query results, by default False
        separation : int | float | u.Quantity, optional
            The separation to use for the SIMBAD query plot, by default None. If a number is given, then it is assumed to be in arcseconds.

        Returns
        -------
        astropy.table.Table | List[astropy.table.Table]
            The SIMBAD query(ies) table(s) for the subject(s).
        """

        subject_metadata = self.getSubjectMetadata(subject_input)

        if(subject_metadata is None):
            warnings.warn(f"Subject ID {subject_input} was not found, so a SIMBAD query cannot be performed: Returning None")
            return None

        RA = subject_metadata["RA"]
        DEC = subject_metadata["DEC"]
        coords = [RA, DEC]

        if(search_type == "Box" or search_type == "Box Search"):
            search_parameters = {"Coordinates": coords, "Type": search_type, "FOV": FOV}
        elif(search_type == "Cone" or search_type == "Cone Search"):
            search_parameters = {"Coordinates": coords, "Type": search_type, "radius": radius}
        else:
            raise ValueError(f"Invalid search type: {search_type}. Expected 'Cone', 'Cone Search', 'Box', or 'Box Search'.")

        simbad_searcher = SimbadSearcher(search_parameters)

        result = simbad_searcher.getQuery()

        if(plot):
            simbad_searcher.plotEntries(separation=separation)

        return result

    @multioutput
    @uses_subject_ids
    def getConditionalSimbadQueryForSubject(self, subject_input: subject_ids_input_typing, search_type: str = "Box Search", FOV: astropy_quantity_input_typing = 120*u.arcsec, radius: astropy_quantity_input_typing = 60*u.arcsec, otypes: Union[str, List[str]] = ["BD*", "BD?", "BrownD*", "BrownD?", "BrownD*_Candidate", "PM*"], plot: bool = False, separation: astropy_quantity_input_typing = None) -> Union[Table, List[Table]]:
        """
        Gets the conditional SIMBAD query for the subject.
        
        Parameters
        ----------
        subject_input :  str | int | TextIOWrapper | TextIO | DataFrame | Iterable[str | int]
            The subject ID(s) to get the conditional SIMBAD query for.
        search_type : str, optional
            The type of SIMBAD search to perform, by default "Box Search". The options are "Box Search" and "Cone Search".
        FOV : int | float | u.Quantity, optional
            The field of view to search in, by default 120*u.arcsec. If a number is given, then it is assumed to be in arcseconds.
        radius : int | float | u.Quantity, optional
            The radius to search in, by default 60*u.arcsec. If a number is given, then it is assumed to be in arcseconds.
        otypes : str | List[str], optional
            The object types to search for, by default ["BD*", "BD?", "BrownD*", "BrownD?", "BrownD*_Candidate", "PM*"]
        plot : bool, optional
            Whether to plot the SIMBAD query results, by default False
        separation : int | float | u.Quantity, optional
            The separation to use for the SIMBAD query plot, by default None. If a number is given, then it is assumed to be in arcseconds.
        Returns
        -------
        astropy.table.Table | List[astropy.table.Table]
            The conditional SIMBAD query(ies) table(s) for the subject(s).
            
        Notes
        -----
        The conditional SIMBAD query is a SIMBAD query that is performed on the subject's coordinates and reduces the query based on the otypes given.
        Additionally, the conditional SIMBAD query is performed on a larger field of view than the regular SIMBAD query to account for high proper motion objects.
        """
        
        
        subject_metadata = self.getSubjectMetadata(subject_input)

        if (subject_metadata is None):
            warnings.warn(f"Subject ID {subject_input} was not found, so a conditional SIMBAD query cannot be performed: Returning None")
            return None

        RA = subject_metadata["RA"]
        DEC = subject_metadata["DEC"]
        coords = [RA, DEC]

        # Introduce a buffer to the FOV to more reliably capture high proper motion objects
        extreme_proper_motion = 5 * u.arcsec / u.yr
        current_epoch = float(Time.now().decimalyear) * u.yr
        simbad_epoch = 2000 * u.yr
        time_difference = current_epoch - simbad_epoch
        max_separation = extreme_proper_motion * time_difference
        buffer_FOV = 2 * max_separation
        buffer_radius = max_separation

        if (search_type == "Box" or search_type == "Box Search"):
            search_parameters = {"Coordinates": coords, "Type": search_type, "FOV": FOV + buffer_FOV}
        elif (search_type == "Cone" or search_type == "Cone Search"):
            search_parameters = {"Coordinates": coords, "Type": search_type, "radius": radius + buffer_radius}
        else:
            raise ValueError(f"Invalid search type: {search_type}. Expected 'Cone', 'Cone Search', 'Box', or 'Box Search'.")

        simbad_searcher = SimbadSearcher(search_parameters)
        otypes_condition = simbad_searcher.buildConditionalArgument("OTYPES", "==", otypes)
        conditions = [otypes_condition]
        result = simbad_searcher.getConditionalQuery(conditions)

        if(plot):
            simbad_searcher.plotEntries(separation=separation)

        return result

    @multioutput
    @uses_subject_ids
    def sourceExistsInSimbadForSubject(self, subject_input: subject_ids_input_typing, search_type: str = "Box Search", FOV: astropy_quantity_input_typing = 120*u.arcsec, radius: astropy_quantity_input_typing = 60*u.arcsec) -> bool:
        """
        Checks if there is any source in SIMBAD for the subject.
        
        Parameters
        ----------
        subject_input :  str | int | TextIOWrapper | TextIO | DataFrame | Iterable[str | int]
            The subject ID(s) to check if there is any source in SIMBAD for.
        search_type : str, optional
            The type of SIMBAD search to perform, by default "Box Search". The options are "Box Search" and "Cone Search".
        FOV : int | float | u.Quantity, optional
            The field of view to search in, by default 120*u.arcsec. If a number is given, then it is assumed to be in arcseconds.
        radius : int | float | u.Quantity, optional
            The radius to search in, by default 60*u.arcsec. If a number is given, then it is assumed to be in arcseconds.
    
        Returns
        -------
        bool
            Whether there is any source in SIMBAD for the subject.
        
        Notes
        -----
        This method is a wrapper for the getSimbadQueryForSubject method that checks if there is any source in SIMBAD for the subject.
        """
        
        return len(self.getSimbadQueryForSubject(subject_input, search_type=search_type, FOV=FOV, radius=radius)) > 0

    # Gaia query methods
    @multioutput
    @uses_subject_ids
    def getGaiaQueryForSubject(self, subject_input: subject_ids_input_typing, search_type: str = "Box Search", FOV: astropy_quantity_input_typing = 120*u.arcsec, radius: astropy_quantity_input_typing = 60*u.arcsec, plot: bool = False, separation: astropy_quantity_input_typing = None) -> Union[Table, List[Table]]:
        """
        Gets the Gaia query for the subject.
        
        Parameters
        ----------
        subject_input :  str | int | TextIOWrapper | TextIO | DataFrame | Iterable[str | int]
            The subject ID(s) to get the Gaia query for.
        search_type : str, optional
            The type of Gaia search to perform, by default "Box Search". The options are "Box Search" and "Cone Search".
        FOV : int | float | u.Quantity, optional
            The field of view to search in, by default 120*u.arcsec. If a number is given, then it is assumed to be in arcseconds.
        radius : int | float | u.Quantity, optional
            The radius to search in, by default 60*u.arcsec. If a number is given, then it is assumed to be in arcseconds.
        plot : bool, optional
            Whether to plot the Gaia query results, by default False
        separation : int | float | u.Quantity, optional
            The separation to use for the Gaia query plot, by default None. If a number is given, then it is assumed to be in arcseconds.
        
        Returns
        -------
        astropy.table.Table | List[astropy.table.Table]
            The Gaia query(ies) table(s) for the subject(s).
        """
        
        # Get the subject's metadata
        subject_metadata = self.getSubjectMetadata(subject_input)

        if (subject_metadata is None):
            warnings.warn(f"Subject ID {subject_input} was not found, so a Gaia query cannot be performed: Returning None")
            return None

        RA = subject_metadata["RA"]
        DEC = subject_metadata["DEC"]
        coords = [RA, DEC]

        if (search_type == "Box" or search_type == "Box Search"):
            search_parameters = {"Coordinates": coords, "Type": search_type, "FOV": FOV}
        elif (search_type == "Cone" or search_type == "Cone Search"):
            search_parameters = {"Coordinates": coords, "Type": search_type, "radius": radius}
        else:
            raise ValueError(f"Invalid search type: {search_type}. Expected 'Cone', 'Cone Search', 'Box', or 'Box Search'.")

        gaia_searcher = GaiaSearcher(search_parameters)

        result = gaia_searcher.getQuery()

        if (plot):
            gaia_searcher.plotEntries(separation=separation)

        return result

    @multioutput
    @uses_subject_ids
    def getConditionalGaiaQueryForSubject(self, subject_input: subject_ids_input_typing, search_type: str = "Box Search", FOV: astropy_quantity_input_typing = 120*u.arcsec, radius: astropy_quantity_input_typing = 60*u.arcsec, gaia_pm: astropy_quantity_input_typing = 100 * u.mas / u.yr, plot: bool = False, separation: astropy_quantity_input_typing = None) -> Union[Table, List[Table]]:
        """
        Gets the conditional Gaia query for the subject.

        Parameters
        ----------
        subject_input :  str | int | TextIOWrapper | TextIO | DataFrame | Iterable[str | int]
            The subject ID(s) to get the conditional Gaia query for.
        search_type : str, optional
            The type of Gaia search to perform, by default "Box Search". The options are "Box Search" and "Cone Search".
        FOV : int | float | u.Quantity, optional
            The field of view to search in, by default 120*u.arcsec. If a number is given, then it is assumed to be in arcseconds.
        radius : int | float | u.Quantity, optional
            The radius to search in, by default 60*u.arcsec. If a number is given, then it is assumed to be in arcseconds.
        gaia_pm : int | float | u.Quantity, optional
            The proper motion to use for the conditional Gaia query, by default 100*u.mas/u.yr. If a number is given, then it is assumed to be in milliarcseconds per year.

        Returns
        -------
        astropy.table.Table | List[astropy.table.Table]
            The conditional Gaia query(ies) table(s) for the subject(s).

        Notes
        -----
        The conditional Gaia query is a Gaia query that is performed on the subject's coordinates and reduces the query based on the proper motion given.
        Additionally, the conditional Gaia query is performed on a larger field of view than the regular Gaia query to account for high proper motion objects.
        """

        subject_metadata = self.getSubjectMetadata(subject_input)

        if (subject_metadata is None):
            warnings.warn(f"Subject ID {subject_input} was not found, so a conditional Gaia query cannot be performed: Returning None")
            return None

        RA = subject_metadata["RA"]
        DEC = subject_metadata["DEC"]
        coords = [RA, DEC]

        # Introduce a buffer to the FOV to more reliably capture high proper motion objects
        extreme_proper_motion = 5 * u.arcsec / u.yr
        current_epoch = float(Time.now().decimalyear) * u.yr
        gaia_epoch = 2016 * u.yr
        time_difference = current_epoch - gaia_epoch
        max_separation = extreme_proper_motion * time_difference
        buffer_FOV = 2 * max_separation
        buffer_radius = max_separation

        if (search_type == "Box" or search_type == "Box Search"):
            search_parameters = {"Coordinates": coords, "Type": search_type, "FOV": FOV + buffer_FOV}
        elif (search_type == "Cone" or search_type == "Cone Search"):
            search_parameters = {"Coordinates": coords, "Type": search_type, "radius": radius + buffer_radius}
        else:
            raise ValueError(
                f"Invalid search type: {search_type}. Expected 'Cone', 'Cone Search', 'Box', or 'Box Search'.")

        gaia_searcher = GaiaSearcher(search_parameters)
        proper_motion_condition = gaia_searcher.buildConditionalArgument("pm", ">=", gaia_pm)
        result = gaia_searcher.getConditionalQuery(proper_motion_condition)

        if (plot):
            gaia_searcher.plotEntries(separation=separation)

        return result

    @multioutput
    @uses_subject_ids
    def sourceExistsInGaiaForSubject(self, subject_input: subject_ids_input_typing, search_type: str = "Box Search", FOV: astropy_quantity_input_typing = 120*u.arcsec, radius: astropy_quantity_input_typing = 60*u.arcsec) -> bool:
        """
        Checks if the source exists in Gaia for the subject.

        Parameters
        ----------
        subject_input :  str | int | TextIOWrapper | TextIO | DataFrame | Iterable[str | int]
            The subject ID(s) to check if the source exists in Gaia for.
        search_type : str, optional
            The type of Gaia search to perform, by default "Box Search". The options are "Box Search" and "Cone Search".
        FOV : int | float | u.Quantity, optional
            The field of view to search in, by default 120*u.arcsec. If a number is given, then it is assumed to be in arcseconds.
        radius : int | float | u.Quantity, optional
            The radius to search in, by default 60*u.arcsec. If a number is given, then it is assumed to be in arcseconds.

        Returns
        -------
        bool
            Whether or not the source exists in Gaia for the subject.

        Notes
        -----
        This method is a wrapper for the getGaiaQueryForSubject method that checks if the Gaia query returns any results.
        """

        return len(self.getGaiaQueryForSubject(subject_input, search_type=search_type, FOV=FOV, radius=radius)) > 0

    # Methods related to selecting acceptable subjects as candidates for review
    # ------------------------------------------------------------------------------------------------------------------
    # Subject type methods
    @staticmethod
    @multioutput
    def bitmaskToSubjectType(bitmask: Union[int, str, List[int], List[str]]) -> Union[str, List[str]]:
        """
        Converts a bitmask value to a subject type.

        Parameters
        ----------
        bitmask : int | str | List[int] | List[str]
            The bitmask value(s) to convert to its subject type(s).

        Returns
        -------
        str | List[str]
            The subject type(s) associated with the bitmask value(s).

        Notes
        -----
        The bitmask values and their associated subject types are as follows:
        1: SMDET Candidate
        2: Blank
        4: Known Brown Dwarf
        8: Quasar
        16: Random Sky Location
        32: White Dwarf
        """

        # Convert the bitmask to an integer
        try:
            bitmask = int(bitmask)
        except ValueError:
            raise ValueError("bitmask must be an integer or a string that can be converted to an integer.")

        # Initialize the bitmask dictionary
        bitmask_dict = {2**0: "SMDET Candidate", 2**1: "Blank", 2**2: "Known Brown Dwarf", 2**3: "Quasar", 2**4: "Random Sky Location", 2**5: "White Dwarf"}

        # Return the bitmask type associated with the bitmask value
        return bitmask_dict.get(bitmask, None)

    @multioutput
    @uses_subject_ids
    def getSubjectType(self, subject_input: subject_ids_input_typing) -> Union[str, List[str]]:
        """
        Gets the subject type for the subject.

        Parameters
        ----------
        subject_input :  str | int | TextIOWrapper | TextIO | DataFrame | Iterable[str | int]
            The subject ID(s) to get the subject type for.

        Returns
        -------
        str | List[str]
            The subject type(s) for the subject ID(s).

        Notes
        -----
        The bitmask values and their associated subject types are as follows:
        1: SMDET Candidate
        2: Blank
        4: Known Brown Dwarf
        8: Quasar
        16: Random Sky Location
        32: White Dwarf
        """

        # Get the bitmask for the subject
        bitmask = self.getSubjectMetadataField(subject_input, "#BITMASK")

        if(bitmask is None):
            warnings.warn(f"Subject ID {subject_input} was not found, so a subject type cannot be determined: Returning None")
            return None

        # Convert the bitmask to a subject type
        subject_type = Analyzer.bitmaskToSubjectType(bitmask)

        return subject_type

    # Acceptable candidate methods
    @multioutput
    @uses_subject_ids
    def checkIfCandidateIsAcceptable(self, subject_input: subject_ids_input_typing, acceptance_ratio: float, acceptance_threshold:int = 0, weighted: bool = False) -> Tuple[bool, Dict[str, int]]:
        """
        Checks if the subject candidate is acceptable for review.

        Parameters
        ----------
        subject_input :  str | int | TextIOWrapper | TextIO | DataFrame | Iterable[str | int]
            The subject ID(s) to check if the candidate is acceptable for review.
        acceptance_ratio : float
            The ratio of "yes" to "total" classifications that must be met for the candidate to be acceptable for review.
        acceptance_threshold : int, optional
            The minimum number of "yes" classifications that must be met for the candidate to be acceptable for review, by default 0.
        weighted : bool, optional
            Whether to use the weighted classifications, by default False.

        Returns
        -------
        bool
            Whether the candidate is acceptable for review.
        Dict[str, int]
            The number of "yes", "no", and "total" classifications for the subject.
        """

        subject_type = self.getSubjectType(subject_input)
        subject_classifications = self.getClassificationsForSubject(subject_input, weighted=weighted)

        if (subject_classifications is None):
            warnings.warn(f"Subject ID {subject_input} was not found, so an acceptable candidate cannot be determined: Returning None")
            return None, None

        # Count the number of successful classifications for each of the bitmask types

        total_classifications = subject_classifications["total"]
        movement_ratio = subject_classifications["yes"] / total_classifications
        non_movement_ratio = subject_classifications["no"] / total_classifications
        if (subject_type == "SMDET Candidate"):
            return (movement_ratio > acceptance_ratio) and (subject_classifications["yes"] > acceptance_threshold), subject_classifications
        else:
            return False, subject_classifications

    def findAcceptableCandidates(self, acceptance_ratio: float, acceptance_threshold: int = 0, weighted: bool = False, save: bool = True, verbose: bool = False) -> List[int]:
        """
        Finds the acceptable candidates for review.

        Parameters
        ----------
        acceptance_ratio : float
            The ratio of "yes" to "total" classifications that must be met for the candidate to be acceptable for review.
        acceptance_threshold : int, optional
            The minimum number of "yes" classifications that must be met for the candidate to be acceptable for review, by default 0.
        weighted : bool, optional
            Whether to use the weighted classifications, by default False.
        save : bool, optional
            Whether to save the acceptable candidates to a CSV file, by default True.
        verbose : bool, optional
            Whether to print the acceptable candidates, by default False.

        Returns
        -------
        List[int]
            The acceptable candidates for review.
        """

        subject_ids = self.getSubjectIDs()
        accepted_subjects = []

        for subject_id in subject_ids:
            acceptable_boolean, subject_classifications_dict = self.checkIfCandidateIsAcceptable(subject_id, acceptance_ratio=acceptance_ratio, acceptance_threshold=acceptance_threshold, weighted=weighted)

            if(acceptable_boolean is None or subject_classifications_dict is None):
                warnings.warn(f"Subject ID {subject_id} was not found, so it cannot be determined if it is an acceptable candidate: Skipping")
                continue

            if (acceptable_boolean):
                if(verbose):
                    print("Subject " + str(subject_id) + f" is an acceptable candidate: {subject_classifications_dict}")
                accepted_subjects.append(subject_id)

        if(save):
            acceptable_candidates_dataframe = self.combineSubjectDataframes(self.getSubjectDataframe(accepted_subjects))
            Analyzer.saveSubjectDataframeToFile(acceptable_candidates_dataframe, f"acceptable_candidates_acceptance_ratio_{acceptance_ratio}_acceptance_threshold_{acceptance_threshold}.csv")

        return accepted_subjects

    def sortAcceptableCandidatesByDatabase(self, accepted_subjects: List[int], verbose: bool = False) -> List[str]:
        """
        Sorts and rejects the acceptable candidates by database.

        Parameters
        ----------

        accepted_subjects : List[int]
            The acceptable candidates for review.
        verbose : bool, optional
            Whether to print the acceptable candidates, by default False.

        Returns
        -------
        List[str]
            The csv filenames for the acceptable candidates which were not found in the respective databases.

        Notes
        -----
        The csv filenames are as follows:
        - not_in_simbad_subjects.csv
        - not_in_gaia_subjects.csv
        - not_in_either_subjects.csv
        """

        not_in_simbad_subjects = []
        not_in_gaia_subjects = []
        not_in_either_subjects = []

        with tqdm(total=len(accepted_subjects), unit="Subject") as pbar:
            for index, subject_id in enumerate(accepted_subjects):
                if(verbose):
                    print("Checking subject " + str(subject_id) + f" ({index + 1} out of {len(accepted_subjects)})")
                database_check_dict, database_query_dict = self.checkSubjectFOV(subject_id)
                no_database = not any(database_check_dict.values())
                if (no_database):
                    if(verbose):
                        print(f"Subject {subject_id} is not in either database.")
                    not_in_either_subjects.append(subject_id)
                    not_in_simbad_subjects.append(subject_id)
                    not_in_gaia_subjects.append(subject_id)
                else:
                    for database_name, in_database in database_check_dict.items():
                        if (not in_database):
                            if (database_name == "SIMBAD"):
                                if(verbose):
                                    print(f"Subject {subject_id} is not in SIMBAD.")
                                not_in_simbad_subjects.append(subject_id)
                            elif (database_name == "Gaia"):
                                if(verbose):
                                    print(f"Subject {subject_id} is not in Gaia.")
                                not_in_gaia_subjects.append(subject_id)
                        else:
                            if (database_name == "SIMBAD"):
                                if(verbose):
                                    print(f"Subject {subject_id} is in SIMBAD.")
                            elif (database_name == "Gaia"):
                                if(verbose):
                                    print(f"Subject {subject_id} is in Gaia.")
                pbar.update(1)

        not_in_simbad_subject_dataframes = self.getSubjectDataframe(not_in_simbad_subjects)
        not_in_gaia_subject_dataframes = self.getSubjectDataframe(not_in_gaia_subjects)
        not_in_either_subject_dataframes = self.getSubjectDataframe(not_in_either_subjects)

        not_in_simbad_subjects_dataframe = self.combineSubjectDataframes(not_in_simbad_subject_dataframes)
        not_in_gaia_subjects_dataframe = self.combineSubjectDataframes(not_in_gaia_subject_dataframes)
        not_in_either_subjects_dataframe = self.combineSubjectDataframes(not_in_either_subject_dataframes)

        self.saveSubjectDataframeToFile(not_in_simbad_subjects_dataframe, "not_in_simbad_subjects.csv")
        self.saveSubjectDataframeToFile(not_in_gaia_subjects_dataframe, "not_in_gaia_subjects.csv")
        self.saveSubjectDataframeToFile(not_in_either_subjects_dataframe, "not_in_either_subjects.csv")

        generated_files = ["not_in_simbad_subjects.csv", "not_in_gaia_subjects.csv", "not_in_either_subjects.csv"]
        return generated_files

    # Primary method for getting acceptable candidates and sorting them by database
    def performCandidatesSort(self, acceptance_ratio: float = 0.5, acceptance_threshold: int = 0, weighted: bool = False, acceptable_candidates_csv: str = None) -> None:
        """
        Performs the candidate generation and sorting process.

        Parameters
        ----------
        acceptance_ratio : float, optional
            The ratio of "yes" classifications to "total" classifications required for a subject to be considered an acceptable candidate, by default 0.5
        acceptance_threshold : int, optional
            The minimum number of "yes" classifications required for a subject to be considered an acceptable candidate, by default 0
        weighted : bool, optional
            Whether to use the weighted classifications, by default False
        acceptable_candidates_csv : str, optional
            The path to the acceptable candidates csv file, by default None. If None, a new acceptable candidates csv file will be generated.

        Notes
        -----
        The resulting csv filenames are as follows:
        - not_in_simbad_subjects.csv
        - not_in_gaia_subjects.csv
        - not_in_either_subjects.csv
        """

        acceptable_candidates = []
        if (acceptable_candidates_csv is not None and os.path.exists(acceptable_candidates_csv)):
            print("Found acceptable candidates file.")
            acceptable_candidates_dataframe = self.loadSubjectDataframeFromFile(acceptable_candidates_csv)
            acceptable_candidates = acceptable_candidates_dataframe["subject_id"].values
        elif (acceptable_candidates_csv is None):
            print("No acceptable candidates file found. Generating new one.")
            acceptable_candidates = self.findAcceptableCandidates(acceptance_ratio=acceptance_ratio, acceptance_threshold=acceptance_threshold, weighted=weighted)
        elif (not os.path.exists(acceptable_candidates_csv)):
            raise FileNotFoundError(f"Cannot find acceptable candidates file: {acceptable_candidates_csv}")

        generated_files = self.sortAcceptableCandidatesByDatabase(acceptable_candidates)
        print("Generated files: " + str(generated_files))

    # FOV search and checking methods
    @multioutput
    @uses_subject_ids
    def searchSubjectFOV(self, subject_input: subject_ids_input_typing) -> Tuple[bool, Dict[str, bool]]:
        """
        Searches the subject's FOV for known objects in SIMBAD and Gaia.

        Parameters
        ----------
        subject_input : str | int | TextIOWrapper | TextIO | DataFrame | Iterable[str | int]
            The subject(s) to search.

        Returns
        -------
        Tuple[bool, Dict[str, bool]]
            A tuple containing a boolean indicating whether the subject's FOV has any known objects in it, and a dictionary containing the results of the search for each database.
        """

        FOV = 120 * u.arcsec

        database_check_dict = {"Simbad": self.sourceExistsInSimbadForSubject(subject_input, search_type="Box Search", FOV=FOV), "Gaia": self.sourceExistsInGaiaForSubject(subject_input, search_type="Box Search", FOV=FOV)}

        # For each database, check if the subject's FOV search area has any known objects in it
        if(any(database_check_dict.values())):
            return True, database_check_dict
        else:
            return False, database_check_dict

    @multioutput
    @uses_subject_ids
    def checkSubjectFOV(self, subject_input: subject_ids_input_typing, otypes: List[str] = ["BD*", "BD?", "BrownD*", "BrownD?", "BrownD*_Candidate", "PM*"], gaia_pm: astropy_quantity_input_typing = 100 * u.mas / u.yr) -> Tuple[Dict[str, bool], Dict[str, Table]]:
        """
        Checks the subject's FOV for known objects in SIMBAD and Gaia.

        Parameters
        ----------
        subject_input : str | int | TextIOWrapper | TextIO | DataFrame | Iterable[str | int]
            The subject(s) to check.
        otypes : List[str], optional
            The SIMBAD object types to check for, by default ["BD*", "BD?", "BrownD*", "BrownD?", "BrownD*_Candidate", "PM*"]
        gaia_pm : astropy_quantity_input_typing, optional
            The Gaia proper motion threshold to use, by default 100 * u.mas / u.yr

        Returns
        -------
        Tuple[Dict[str, bool], Dict[str, Table]]

        Notes
        -----
        The resulting dictionary keys are as follows:
        - SIMBAD
        - Gaia
        """

        simbad_query = self.getConditionalSimbadQueryForSubject(subject_input, search_type="Box Search", FOV=120 * u.arcsec, otypes=otypes)
        gaia_query = self.getConditionalGaiaQueryForSubject(subject_input, search_type="Box Search", FOV=120 * u.arcsec, gaia_pm=gaia_pm)

        database_query_dict = {"SIMBAD": simbad_query, "Gaia": gaia_query}
        database_check_dict = {}
        # Check each query to determine if it is empty or None
        for database, query in database_query_dict.items():
            if(query is None):
                database_check_dict[database] = False
            else:
                database_check_dict[database] = len(query) > 0

        return database_check_dict, database_query_dict

    # Acceptance counts method
    def calculateAcceptanceCountsBySubjectType(self, acceptance_ratio: float) -> Dict[str, Dict[str, int]]:
        """
        Calculates the acceptance counts for each subject type.

        Parameters
        ----------
        acceptance_ratio : float
            The ratio of "yes" classifications to "total" classifications required for a subject to be considered an acceptable candidate.

        Returns
        -------
        Dict[str, Dict[str, int]]
            A dictionary containing the acceptance counts for each subject type.

        Notes
        -----
        The resulting dictionary keys are as follows:
        - Known Brown Dwarf
        - Quasar
        - White Dwarf
        - SMDET Candidate
        - Random Sky Location

        Then the dictionary values are dictionaries containing the following keys:
        - success
        - total
        """

        # Get the subject IDs
        subject_ids = self.getSubjectIDs()

        # Initialize the success count dictionary
        success_count_dict = {}

        # Iterate through the subject IDs
        for subject_id in subject_ids:

            subject_type = self.getSubjectType(subject_id)

            # If the bitmask type is None, continue
            if subject_type is None:
                continue

            # If the bitmask type is not in the success count dictionary, add it
            if subject_type not in success_count_dict:
                success_count_dict[subject_type] = {"total": 0, "success": 0}

            # Increment the total count for the bitmask type
            success_count_dict[subject_type]["total"] += 1

            # Get the subject classifications
            subject_classifications = self.getClassificationsForSubject(subject_id)

            # Count the number of successful classifications for each of the bitmask types
            total_classifications = subject_classifications["yes"] + subject_classifications["no"]
            movement_ratio = subject_classifications["yes"] / total_classifications
            non_movement_ratio = subject_classifications["no"] / total_classifications

            if(subject_type == "Known Brown Dwarf"):
                if(movement_ratio >= acceptance_ratio):
                    success_count_dict[subject_type]["success"] += 1
            elif(subject_type == "Quasar"):
                if(non_movement_ratio >= acceptance_ratio):
                    success_count_dict[subject_type]["success"] += 1
            elif(subject_type == "White Dwarf"):
                success_count_dict[subject_type]["success"] = None
            elif(subject_type == "SMDET Candidate"):
                success_count_dict[subject_type]["success"] = None
            elif(subject_type == "Random Sky Location"):
                if (non_movement_ratio >= acceptance_ratio):
                    success_count_dict[subject_type]["success"] += 1

        # Return the success count dictionary
        return success_count_dict

    # Helper methods for saving and loading subject dataframes as needed
    @staticmethod
    def saveSubjectDataframeToFile(subject_dataframe: pd.DataFrame, filename: str) -> None:
        """
        Saves the subject dataframe to a CSV file.

        Parameters
        ----------
        subject_dataframe : pd.DataFrame
            The subject dataframe to save.
        filename : str
            The filename to save the subject dataframe to.
        """

        # Save the subject dataframe to a CSV file
        subject_dataframe.to_csv(filename, index=False)

    @staticmethod
    def loadSubjectDataframeFromFile(filename: str) -> pd.DataFrame:
        """
        Loads the subject dataframe from a CSV file.

        Parameters
        ----------
        filename : str
            The filename to load the subject dataframe from.

        Returns
        -------
        pd.DataFrame
            The subject dataframe.
        """

        # Load the subject dataframe from a CSV file
        subject_dataframe = pd.read_csv(filename)

        # Return the subject dataframe
        return subject_dataframe

    # Methods related to plotting subjects
    # ------------------------------------------------------------------------------------------------------------------
    @uses_subject_ids
    def plotSkyMapForSubjects(self, subject_input: subject_ids_input_typing) -> None:
        """
        Plots a sky map for the given subjects.

        Parameters
        ----------
        subject_input : subject_ids_input_typing
            The subject IDs to plot the sky map for.
        """

        subject_dataframes = self.getSubjectDataframe(subject_input)
        subject_dataframe = self.combineSubjectDataframes(subject_dataframes)
        subject_dataframe.to_csv("temp.csv", index=False)
        subject_csv_plotter = SubjectCSVPlotter("temp.csv")
        subject_csv_plotter.plot()
        os.remove("temp.csv")

    @multioutput
    @uses_subject_ids
    def plotQueryForSubject(self, subject_input: subject_ids_input_typing, database_name: str, show_in_wiseview: bool = False) -> None:
        """
        Plots the query for the given subjects.

        Parameters
        ----------
        subject_input : subject_ids_input_typing
            The subject IDs to plot the query for.
        database_name : str
            The name of the database to query. Must be one of the following: "SIMBAD", "Gaia", "Not in either".
        show_in_wiseview : bool, optional
            Whether to show the subject in WiseView.
        Notes
        -----
        The following databases are supported:
        - SIMBAD
        - Gaia
        """

        subject_dataframe = self.getSubjectDataframe(subject_input)

        for subject_id in subject_dataframe["subject_id"]:
            if(show_in_wiseview):
                self.showSubjectInWiseView(subject_id, True)
            if (database_name.lower() == "simbad"):
                query = self.getSimbadQueryForSubject(subject_id, FOV=120 * u.arcsec, plot=True, separation=60 * u.arcsec)
                print("Simbad: ", query)
            elif (database_name.lower() == "gaia"):
                query = self.getGaiaQueryForSubject(subject_id, FOV=120 * u.arcsec, plot=True, separation=60 * u.arcsec)
                print("Gaia: ", query)
            elif (database_name.lower() == "not in either"):
                query = self.getSimbadQueryForSubject(subject_id, FOV=120 * u.arcsec, plot=True, separation=60 * u.arcsec)
                print("Simbad: ", query)
                query = self.getGaiaQueryForSubject(subject_id, FOV=120 * u.arcsec, plot=True, separation=60 * u.arcsec)
                print("Gaia: ", query)
            else:
                raise ValueError("Invalid database name.")

    @multioutput
    @uses_subject_ids
    def plotConditionalQueriesForSubject(self, subject_input: subject_ids_input_typing, database_name: str, show_in_wiseview: bool = False) -> None:
        """
        Plots the query for the given subjects.

        Parameters
        ----------
        subject_input : subject_ids_input_typing
            The subject IDs to plot the query for.
        database_name : str
            The name of the database to query. Must be one of the following: "SIMBAD", "Gaia", "Not in either".
        show_in_wiseview : bool, optional
            Whether to show the subject in WiseView.
        Notes
        -----
        The following databases are supported:
        - SIMBAD
        - Gaia
        """

        subject_dataframe = self.getSubjectDataframe(subject_input)

        for subject_id in subject_dataframe["subject_id"]:
            if(show_in_wiseview):
                self.showSubjectInWiseView(subject_id, True)
            if (database_name == "Simbad"):
                query = self.getConditionalSimbadQueryForSubject(subject_id, FOV=120 * u.arcsec, plot=True, separation=60 * u.arcsec)
                print("Simbad: ", query)
            elif (database_name == "Gaia"):
                query = self.getConditionalGaiaQueryForSubject(subject_id, FOV=120 * u.arcsec, plot=True, separation=60 * u.arcsec)
                print("Gaia: ", query)
            elif (database_name == "not in either"):
                query = self.getConditionalSimbadQueryForSubject(subject_id, FOV=120 * u.arcsec, plot=True, separation=60 * u.arcsec)
                print("Simbad: ", query)
                query = self.getConditionalGaiaQueryForSubject(subject_id, FOV=120 * u.arcsec, plot=True, separation=60 * u.arcsec)
                print("Gaia: ", query)
            else:
                raise ValueError("Invalid database name.")


class Classifier:

    insufficient_classifications_default_accuracy = 0.5

    def __init__(self, analyzer: Analyzer):
        """
        Initializes a new instance of the Classifier class.

        Parameters
        ----------
        analyzer : Analyzer
            The analyzer to use with the classification information.
        """

        self.user_information = {}
        self.analyzer = analyzer
        print("Calculating user performances...")
        user_identifiers = self.analyzer.getUniqueUserIdentifiers(user_identifier="username", include_logged_out_users=True)
        total_users = len(user_identifiers)
        # Create a progress bar
        progress_bar = tqdm(total=total_users)
        for index, user_identifier in enumerate(user_identifiers):
            ignore_warnings(self.calculateUserPerformance)(user_identifier)
            progress_bar.update(1)
        print("User performances calculated.")

    @multioutput
    @uses_user_identifiers
    def getUserAccuracy(self, user_identifier: user_identifiers_input_typing, default_insufficient_classifications: bool = True) -> Union[float, List[float]]:
        """
        Gets the accuracy of the given user.

        Parameters
        ----------
        user_identifier : user_identifiers_input_typing
            The user identifier to get the accuracy for.
        default_insufficient_classifications : bool, optional
            Whether to return the default accuracy to 0.5, if the user has insufficient classifications.

        Returns
        -------
        float | List[float]
            The accuracy or accuracies of the user or users.
        """

        if (user_identifier not in self.user_information):
            self.calculateUserPerformance(user_identifier)

        user_accuracy = self.user_information[user_identifier]["Performance"]["Accuracy"]
        if (default_insufficient_classifications and user_accuracy is None):
            return self.insufficient_classifications_default_accuracy
        else:
            return user_accuracy


    @multioutput
    @uses_user_identifiers
    def getUserVerifiedClassifications(self, user_identifier: user_identifiers_input_typing) -> Dict[str, Dict[str, int]]:
        """
        Gets the verified classifications of the given user(s).

        Parameters
        ----------
        user_identifier : user_identifiers_input_typing
            The user identifier(s) to get the verified classifications for.

        Returns
        -------
        Dict[str, Dict[str, int]]
            The verified classifications of the user(s).

        Notes
        -----
        The verified classifications are returned in the following format:
        {
            "Classifications": {
                "Known Brown Dwarf": {
                "success": x1,
                "failure": y1,
                "total": x1+y1
                },
                "Quasar": {
                "success": x2,
                "failure": y2,
                "total": x2+y2
                },
                "Random Sky Location": {
                "success": x3,
                "failure": y3,
                "total": x3+y3
                }
            }
        }
        """

        if (user_identifier not in self.user_information):
            self.calculateUserPerformance(user_identifier)
        return self.user_information[user_identifier]["Classifications"]

    @multioutput
    @uses_user_identifiers
    def getUserInformation(self, user_identifier: user_identifiers_input_typing, default_insufficient_classifications: bool = True) -> List[Dict[str, Any]]:
        """
        Gets the information of the given user(s).

        Parameters
        ----------

        user_identifier : user_identifiers_input_typing
            The user identifier(s) to get the information for.
        default_insufficient_classifications : bool, optional
            Whether to return the default accuracy as 0.5, if the user has insufficient classifications.

        Returns
        -------
        List[Dict[str, Any]]
            The information of the user(s).

        Notes
        -----
        Contains the following information in the dictionary or dictionaries:
        Classifications: Dict[str, Dict[str, int]]
            The verified classifications of the user(s).
        Performance: Dict[str, Dict[str, float]]
            The performance of the user(s).
        """

        if (user_identifier not in self.user_information):
            self.calculateUserPerformance(user_identifier)

        user_accuracy = self.user_information[user_identifier]["Performance"]["Accuracy"]
        if(default_insufficient_classifications and user_accuracy is None):
            modified_user_information = copy(self.user_information[user_identifier])
            modified_user_information["Performance"]["Accuracy"] = self.insufficient_classifications_default_accuracy
            return modified_user_information
        else:
            return self.user_information[user_identifier]

    @multioutput
    @uses_user_identifiers
    def calculateUserPerformance(self, user_identifier: user_identifiers_input_typing) -> None:
        """
        Calculates the performance of the given user(s).

        Parameters
        ----------
        user_identifier : user_identifiers_input_typing
            The user identifier(s) to calculate the performance for.

        Notes
        -----
        The performance is calculated as follows:
        Accuracy = (x1 + x2 + x3) / (x1 + x2 + x3 + y1 + y2 + y3)
        Where:
        x1 = Number of (scaled) successful classifications of known brown dwarfs
        x2 = Number of (scaled) successful classifications of quasars
        x3 = Number of (scaled) successful classifications of random sky locations
        y1 = Number of (scaled) failed classifications of known brown dwarfs
        y2 = Number of (scaled) failed classifications of quasars
        y3 = Number of (scaled) failed classifications of random sky locations

        Default scaling for subject types:
        Known Brown Dwarf: 1
        Quasar: 1
        Random Sky Location: 1

        The information is stored in the following format:
        {
            "Classifications": {
                "Known Brown Dwarf": {
                "success": x1,
                "failure": y1,
                "total": x1+y1
                },
                "Quasar": {
                "success": x2,
                "failure": y2,
                "total": x2+y2
                },
                "Random Sky Location": {
                "success": x3,
                "failure": y3,
                "total": x3+y3
                }
            },
            "Performance": {
                "Accuracy": (x1 + x2 + x3) / (x1 + x2 + x3 + y1 + y2 + y3)
            }
        }
        """

        user_classifications_dataframe = self.analyzer.getClassificationsByUser(user_identifier)
        user_information_dictionary = {"Classifications": {}}

        verified_subject_types = {"Known Brown Dwarf": True, "Quasar": False, "Random Sky Location": False}
        verified_subject_performance_scales = {"Known Brown Dwarf": 1, "Quasar": 1, "Random Sky Location": 1}
        for verified_subject_type in verified_subject_types:
            user_information_dictionary["Classifications"][verified_subject_type] = {"total": 0, "success": 0, "failure": 0}

        for index in user_classifications_dataframe.index:
            subject_id = user_classifications_dataframe["subject_id"][index]

            subject_type = self.analyzer.getSubjectType(subject_id)

            if (subject_type is None):
                warnings.warn("Subject type is None for subject ID: " + str(subject_id))
                continue

            try:
                # Try to get the number of "yes" classifications
                yes_count = int(user_classifications_dataframe["data.yes"][index])
            except ValueError:
                # If there are no "yes" classifications, then set the count to 0
                yes_count = 0

            try:
                # Try to get the number of "no" classifications
                no_count = int(user_classifications_dataframe["data.no"][index])
            except ValueError:
                # If there are no "no" classifications, then set the count to 0
                no_count = 0

            classification_dict = {"yes": bool(yes_count), "no": bool(no_count)}

            if(subject_type not in verified_subject_types):
                continue
            else:
                user_information_dictionary["Classifications"][subject_type]["total"] += 1
                should_be_mover = verified_subject_types[subject_type]

                if(should_be_mover == classification_dict["yes"]):
                    user_information_dictionary["Classifications"][subject_type]["success"] += 1
                else:
                    user_information_dictionary["Classifications"][subject_type]["failure"] += 1

        if(user_identifier not in self.user_information):
            # TODO: Investigate Cohen's kappa coefficient for binary classification
            # https://en.wikipedia.org/wiki/Cohen%27s_kappa#:~:text=Cohen's%20kappa%20coefficient%20(%CE%BA%2C%20lowercase,for%20qualitative%20(categorical)%20items.
            # https://www.ncbi.nlm.nih.gov/pmc/articles/PMC3900052/#:~:text=Cohen%20suggested%20the%20Kappa%20result,1.00%20as%20almost%20perfect%20agreement.
            user_information_dictionary["Performance"] = {"Accuracy": 0.0}
            self.user_information[user_identifier] = user_information_dictionary

            total_classifications = 0
            for subject_type in user_information_dictionary["Classifications"]:
                total_classifications += user_information_dictionary["Classifications"][subject_type]["total"]

            # Calculate the accuracy
            if (total_classifications == 0):
                warnings.warn(f"{user_identifier} has no valid classifications for performance. Setting accuracy as None")
                self.user_information[user_identifier]["Performance"]["Accuracy"] = None
            else:
                successful_ratio_total = 0.0
                subject_type_scaling_total = 0.0
                for subject_type in verified_subject_types:
                    if(user_information_dictionary["Classifications"][subject_type]["total"] == 0):
                        continue
                    elif(verified_subject_performance_scales[subject_type] != 0):
                        successful_ratio_total += float(verified_subject_performance_scales[subject_type]) * float(user_information_dictionary["Classifications"][subject_type]["success"]) / float(user_information_dictionary["Classifications"][subject_type]["total"])
                        subject_type_scaling_total += float(verified_subject_performance_scales[subject_type])
                self.user_information[user_identifier]["Performance"]["Accuracy"] = successful_ratio_total / subject_type_scaling_total
        else:
            raise ValueError(f"User identifier {user_identifier} already exists in user performance dictionary.")

    def calculateAllUserPerformances(self, include_logged_out_users: bool = True) -> None:
        """
        Calculates the performance of all users in the database.

        Parameters
        ----------
        include_logged_out_users : bool, optional
            Whether to include logged-out users in the calculation. The default is True.
        """

        user_identifiers = self.analyzer.getUniqueUserIdentifiers(user_identifier="username", include_logged_out_users=include_logged_out_users)
        self.calculateUserPerformance(user_identifiers)

    def getAllUserAccuracies(self, include_logged_out_users: bool = True, default_insufficient_classifications: bool = True) -> List[float]:
        """
        Gets the accuracy of all users in the database.

        Parameters
        ----------
        include_logged_out_users : bool, optional
            Whether to include logged-out users in the calculation. The default is True.
        default_insufficient_classifications : bool, optional
            Whether to default to an accuracy of 0.5 if the user has insufficient classifications. The default is True.

        Returns
        -------
        List[float]
            A list of accuracies for each user.
        """

        user_identifiers = self.analyzer.getUniqueUserIdentifiers(user_identifier="username", include_logged_out_users=include_logged_out_users)
        return self.getUserAccuracy(user_identifiers, default_insufficient_classifications=default_insufficient_classifications)

    def getAllUserInformation(self, include_logged_out_users: bool = True, default_insufficient_classifications: bool = True) -> Dict[str, Dict[str, Any]]:
        """
        Gets the information of all users in the database in as a dictionary.

        Parameters
        ----------
        include_logged_out_users : bool, optional
            Whether to include logged-out users in the calculation. The default is True.
        default_insufficient_classifications : bool, optional
            Whether to default to an accuracy of 0.5 if the user has insufficient classifications. The default is True.

        Returns
        -------
        Dict[str, Dict[str, Any]]
            A dictionary of user information dictionaries, with the username as the key.
        """

        user_identifiers = self.analyzer.getUniqueUserIdentifiers(user_identifier="username", include_logged_out_users=include_logged_out_users)
        user_information_dictionaries = self.getUserInformation(user_identifiers, default_insufficient_classifications=default_insufficient_classifications)
        return {user_identifier: user_information_dictionary for user_identifier, user_information_dictionary  in zip(user_identifiers, user_information_dictionaries)}

    def getMostAccurateUsernames(self, include_logged_out_users: bool = True, default_insufficient_classifications: bool = True, classification_threshold: int = 0, verified_classifications_threshold: int = 0, accuracy_threshold:float = 0.0):
        """
        Gets the usernames of the most accurate users in the database.

        Parameters
        ----------
        include_logged_out_users : bool, optional
            Whether to include logged-out users in the calculation. The default is True.
        default_insufficient_classifications : bool, optional
            Whether to default to an accuracy of 0.5 if the user has insufficient classifications. The default is True.
        classification_threshold : int, optional
            The minimum number of classifications a user must have to be considered. The default is 0.
        verified_classifications_threshold : int, optional
            The minimum number of verified classifications a user must have to be considered. The default is 0.
        accuracy_threshold : float, optional
            The minimum accuracy a user must have to be considered. The default is 0.0.

        Returns
        -------
        List[str]
            A list of usernames of the most accurate users.
        """

        user_information_dictionaries = copy(self.getAllUserInformation(include_logged_out_users=include_logged_out_users, default_insufficient_classifications=default_insufficient_classifications))
        new_user_information_dictionaries = copy(user_information_dictionaries)
        for user_identifier in user_information_dictionaries:
            total_classifications = self.analyzer.getTotalClassificationsByUser(user_identifier)
            total_verified_classifications = 0
            for subject_type in user_information_dictionaries[user_identifier]["Classifications"]:
                total_verified_classifications += user_information_dictionaries[user_identifier]["Classifications"][subject_type]["total"]

            if(total_classifications < classification_threshold):
                del new_user_information_dictionaries[user_identifier]
            elif(total_verified_classifications < verified_classifications_threshold):
                del new_user_information_dictionaries[user_identifier]
            elif(user_information_dictionaries[user_identifier]["Performance"]["Accuracy"] is None):
                del new_user_information_dictionaries[user_identifier]
            elif(user_information_dictionaries[user_identifier]["Performance"]["Accuracy"] < accuracy_threshold):
                del new_user_information_dictionaries[user_identifier]

        most_accurate_usernames = [user_identifier for user_identifier in sorted(new_user_information_dictionaries, key=lambda x: new_user_information_dictionaries[x]["Performance"]["Accuracy"], reverse=True)]
        return most_accurate_usernames


    @multioutput
    @plotting
    @uses_user_identifiers
    def plotUserPerformance(self, user_identifier: user_identifiers_input_typing, **kwargs) -> None:
        """
        Plots the performance of a user.

        Parameters
        ----------
        user_identifier : user_identifiers_input_typing
            The user to plot the performance of.
        **kwargs
            Keyword arguments to pass to the plotting function or to the plotting decorator.
        """

        user_accuracy = self.getUserAccuracy(user_identifier)

        if(user_accuracy is None):
            warnings.warn(f"{user_identifier} has no valid classifications for performance. Skipping plot.")
            return None

        user_classifications = self.getUserVerifiedClassifications(user_identifier)
        user_classification_dataframe = pd.DataFrame.from_dict(user_classifications,  orient="index")
        user_classification_dataframe = user_classification_dataframe.rename(columns={"success": "Success", "failure": "Failure", "total": "Total"})
        formatted_accuracy = round(100 * user_accuracy, 2)

        anonymous = kwargs.pop("anonymous", False)
        if(anonymous):
            user_identifier = "Anonymous"

        user_classification_dataframe.plot.bar(y=["Success", "Failure"], stacked=True, title=f"User Accuracy for {user_identifier}: {formatted_accuracy}%", ax=plt.gca(), **kwargs)

        # Put the success percentage on the top of each bar
        index = 0
        for subject_type, subject_type_row in user_classification_dataframe.iterrows():
            total = subject_type_row["Total"]
            success = subject_type_row["Success"]
            if(total != 0):
                plt.text(index, success, f"{round(100 * success / total, 2)}%", ha="center", va="bottom")
            index += 1

        plt.xticks(rotation=0)
        plt.xlabel("Subject Type")
        plt.ylabel("Number of Classifications")

    def plotAllUsersPerformanceHistogram(self, include_logged_out_users: bool = True, default_insufficient_classifications: bool = True, **kwargs) -> None:
        """
        Plots a histogram of the performance of all users.

        Parameters
        ----------
        include_logged_out_users : bool, optional
            Whether to include logged-out users in the plot. The default is True.
        default_insufficient_classifications : bool, optional
            Whether to default to an accuracy of 0.5 if the user has insufficient classifications. The default is True.
        **kwargs
            Keyword arguments to pass to the plotting function or to the plotting decorator.
        """

        user_accuracies = self.getAllUserAccuracies(include_logged_out_users=include_logged_out_users, default_insufficient_classifications=default_insufficient_classifications)
        kwargs["title"] = "User Performance Histogram"
        self.plotPerformanceHistogram(user_accuracies, **kwargs)

    def plotTopUsersPerformanceHistogram(self, classification_threshold: int = None, percentile: float = None, default_insufficient_classifications: bool = True, **kwargs) -> None:
        """
        Plots a histogram of the performance of the top users.

        Parameters
        ----------
        classification_threshold : int, optional
            The minimum number of classifications a user must have to be considered. The default is None.
        percentile : float, optional
            The percentile of users to consider. The default is None.
        default_insufficient_classifications : bool, optional
            Whether to default to an accuracy of 0.5 if the user has insufficient classifications. The default is True.
        **kwargs
            Keyword arguments to pass to the plotting function or to the plotting decorator.
        """

        top_usernames = self.analyzer.getTopUsernames(classification_threshold=classification_threshold, percentile=percentile)
        top_user_accuracies = self.getUserAccuracy(top_usernames, default_insufficient_classifications=default_insufficient_classifications)

        if(classification_threshold is not None):
            kwargs["title"] = f"Performance Histogram: Users With More Than {classification_threshold} Classifications"
        elif(percentile is not None):
            kwargs["title"] = f"Performance Histogram: Top {100-percentile}% of Users by Classifications"

        self.plotPerformanceHistogram(top_user_accuracies, **kwargs)

    @staticmethod
    @plotting
    def plotPerformanceHistogram(accuracies: List[Union[float, None]], **kwargs) -> None:
        """
        Plots a histogram of the performance of the users.

        Parameters
        ----------
        accuracies : List[float | None]

        **kwargs
            Keyword arguments to pass to the plotting function or to the plotting decorator.
        """

        # Remove None accuracies
        accuracy_values = [x for x in accuracies if x is not None]
        bins = kwargs.pop("bins", 20)
        plt.hist(accuracy_values, bins=bins, **kwargs)
        plt.xlabel("User Accuracy", fontsize=14)
        plt.ylabel("Number of Users", fontsize=14)

        # Include a legend which shows the total number of users
        plt.legend([f"Total Users: {len(accuracy_values)}"], fontsize=14)

    @plotting
    def plotTopUsersPerformances(self, classification_threshold: int = None, percentile: float = None, default_insufficient_classifications:bool = True, **kwargs) -> None:
        """
        Plots the performance of the top users.

        Parameters
        ----------
        classification_threshold : int, optional
            The minimum number of classifications a user must have to be considered. The default is None.
        percentile : float, optional
            The percentile of users to consider. The default is None.
        default_insufficient_classifications : bool, optional
            Whether to default to an accuracy of 0.5 if the user has insufficient classifications. The default is True.
        **kwargs
            Keyword arguments to pass to the plotting function or to the plotting decorator.
        """

        top_usernames = self.analyzer.getTopUsernames(classification_threshold=classification_threshold, percentile=percentile)

        top_user_accuracies = self.getUserAccuracy(top_usernames, default_insufficient_classifications=default_insufficient_classifications)

        # Sort the accuracies and usernames by accuracy
        top_user_accuracies, top_usernames = zip(*sorted(zip(top_user_accuracies, top_usernames), reverse=True))

        # Generate x-coordinates for the bars
        x = np.arange(len(top_usernames))

        # Create the bar plot
        ax = plt.gca()

        # Set the default title
        if (percentile is not None):
            plt.title(f"Users in the Top {100 - percentile}% of Classifications")
        elif (classification_threshold is not None):
            plt.title(f"Users with More Than {classification_threshold} Classifications")

        # Set the default y label
        plt.ylabel("User Accuracy", fontsize=15)

        anonymous = kwargs.pop("anonymous", False)

        if (not anonymous):
            ax.set_xticks(x)
            ax.set_xticklabels(top_usernames, ha='right', va='top', rotation=45, color="black")
        else:
            ax.set_xticks([])
            ax.set_xticklabels([])

        bars = ax.bar(x, top_user_accuracies, **kwargs)
        for i, bar in enumerate(bars):
            # Display the user's accuracy above the bar
            offset = 0.01
            ax.text(bar.get_x() + bar.get_width() / 2, top_user_accuracies[i] + offset, f"{round(100*top_user_accuracies[i], 2)}%", horizontalalignment='center', verticalalignment='bottom', fontsize=10)

        # Include a legend which shows the total number of users
        plt.legend([f"Total Users: {len(top_user_accuracies)}"], fontsize=14)

    @plotting
    def plotMostAccurateUsers(self, include_logged_out_users: bool = True, default_insufficient_classifications: bool = True, classification_threshold: int = 0, verified_classifications_threshold: int = 0, accuracy_threshold:float = 0.0, **kwargs) -> None:
        """
        Plots the most accurate users.

        Parameters
        ----------
        include_logged_out_users : bool, optional
            Whether to include logged-out users. The default is True.
        default_insufficient_classifications : bool, optional
            Whether to default to an accuracy of 0.5 if the user has insufficient classifications. The default is True.
        classification_threshold : int, optional
            The minimum number of classifications a user must have to be considered. The default is 0.
        verified_classifications_threshold : int, optional
            The minimum number of verified classifications a user must have to be considered. The default is 0.
        accuracy_threshold : float, optional
            The minimum accuracy a user must have to be considered. The default is 0.0.
        **kwargs
            Keyword arguments to pass to the plotting function or to the plotting decorator.
        """

        most_accurate_users = self.getMostAccurateUsernames(include_logged_out_users=include_logged_out_users, default_insufficient_classifications=default_insufficient_classifications, classification_threshold=classification_threshold, verified_classifications_threshold=verified_classifications_threshold, accuracy_threshold=accuracy_threshold)

        most_accurate_users_accuracies = self.getUserAccuracy(most_accurate_users, default_insufficient_classifications=default_insufficient_classifications)

        # Generate x-coordinates for the bars
        x = np.arange(len(most_accurate_users_accuracies))

        # Create the bar plot
        ax = plt.gca()

        # Set the default title
        plt.title(f"Most Accurate Users with {classification_threshold}+ Classifications and {verified_classifications_threshold}+ Verified Classifications")

        # Set the default y label
        plt.ylabel("User Accuracy", fontsize=15)

        anonymous = kwargs.pop("anonymous", False)

        if (not anonymous):
            ax.set_xticks(x)
            ax.set_xticklabels(most_accurate_users, ha='right', va='top', rotation=45, color="black")
        else:
            ax.set_xticks([])
            ax.set_xticklabels([])

        bars = ax.bar(x, most_accurate_users_accuracies, **kwargs)

        user_accuracies_fontsize = 8
        offset = 0.005
        show_accuracy = True
        for i, bar in enumerate(bars):
            # Display the user's accuracy above the bar
            if(show_accuracy or abs(most_accurate_users_accuracies[i-1] - most_accurate_users_accuracies[i]) >= 0.02):
                ax.text(bar.get_x() + bar.get_width() / 2, most_accurate_users_accuracies[i] + offset,f"{round(100 * most_accurate_users_accuracies[i], 1)}%", horizontalalignment='center', verticalalignment='bottom', fontsize=user_accuracies_fontsize)
                show_accuracy = False
            else:
                show_accuracy = True

        # Include a legend which shows the total number of users
        plt.legend([f"Total Users: {len(most_accurate_users_accuracies)}"], fontsize=14)

    @plotting
    def plotAccuracyVsClassificationTotals(self, include_logged_out_users: bool = True, default_insufficient_classifications:bool = True, log_plot:bool = True, classification_threshold: int = 0, verified_classifications_threshold: int  = 0, accuracy_threshold: float = 0.0, **kwargs) -> None:
        """
        Plots the accuracy of users against the number of classifications they have made.

        Parameters
        ----------
        include_logged_out_users : bool, optional
            Whether to include logged-out users. The default is True.
        default_insufficient_classifications : bool, optional
            Whether to default to an accuracy of 0.5 if the user has insufficient classifications. The default is True.
        log_plot : bool, optional
            Whether to plot the x-axis on a log scale. The default is True.
        classification_threshold : int, optional
            The minimum number of classifications a user must have to be considered. The default is 0.
        verified_classifications_threshold : int, optional
            The minimum number of verified classifications a user must have to be considered. The default is 0.
        accuracy_threshold : float, optional
            The minimum accuracy a user must have to be considered. The default is 0.0.
        **kwargs
            Keyword arguments to pass to the plotting function or to the plotting decorator.
        """

        usernames = self.getMostAccurateUsernames(include_logged_out_users=include_logged_out_users, default_insufficient_classifications=default_insufficient_classifications, classification_threshold=classification_threshold, verified_classifications_threshold=verified_classifications_threshold, accuracy_threshold=accuracy_threshold)
        accuracies = self.getUserAccuracy(usernames, default_insufficient_classifications=default_insufficient_classifications)
        classification_totals = self.analyzer.getTotalClassificationsByUser(usernames)

        plt.scatter(classification_totals, accuracies, c=accuracies, cmap='viridis', edgecolor='none', **kwargs)
        plt.colorbar(label="User Accuracy")
        if(classification_threshold != 0 or verified_classifications_threshold != 0):
            plt.title(f"Accuracy vs. Number of Classifications: {classification_threshold}+ Classifications and {verified_classifications_threshold}+ Verified Classifications")
        else:
            plt.title("Accuracy vs. Number of Classifications")
        plt.xlabel("Number of Classifications", fontsize=14)
        if(log_plot):
            plt.xscale("log")
        plt.ylabel("User Accuracy", fontsize=14)

        # Include a legend which shows the total number of users
        plt.legend([f"Total Users: {len(usernames)}"], fontsize=14)









