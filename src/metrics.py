import pandas as pd
import numpy as np
from sklearn.metrics import accuracy_score, cohen_kappa_score, f1_score, confusion_matrix
from utils.wsi_prostate_cancer_utils import calc_wsi_prostate_cancer_metrics, calc_gleason_grade

class MetricCalculator():
    def __init__(self, model, data_gen, config, mode):
        self.model = model
        self.data_gen = data_gen
        self.mode = mode
        self.dataset_type = config['data']['dataset_type']
        self.num_classes = config['data']['num_classes']
        self.metrics_patch_level = config['model']['metrics_patch_level']
        self.metrics_wsi_level = config['model']['metrics_wsi_level']
        if mode == 'val':
            self.val_gen = data_gen.validation_generator
            self.val_df = data_gen.val_df
            self.test_gen = self.val_gen
            self.test_df = self.val_df
        else:
            self.val_gen = data_gen.validation_generator
            self.val_df = data_gen.val_df
            self.test_gen = data_gen.test_generator
            self.test_df = data_gen.test_df

    def calc_metrics(self):
        print('Calculate metrics for ' + self.mode)
        val_predictions, test_predictions = self.get_predictions()
        metrics = {}
        confusion_matrices = {}
        if self.metrics_patch_level:
            metrics.update(self.calc_patch_level_metrics(test_predictions))
        if self.metrics_wsi_level:
            wsi_metrics, confusion_matrices = self.calc_optimal_wsi_metrics(val_predictions, test_predictions)
            metrics.update(wsi_metrics)
        metrics = self.add_prefix(metrics, self.mode)
        confusion_matrices = self.add_prefix(confusion_matrices, self.mode)
        print('Metrics ' + self.mode)
        print(metrics)
        return metrics, confusion_matrices

    def get_predictions(self):
        model = self.model
        data_gen = self.data_gen
        batch_size = data_gen.validation_generator.batch_size
        if self.mode == 'val':
            val_gen = self.val_gen
            val_predictions = model.predict(val_gen, batch_size=batch_size, steps=np.ceil(val_gen.n / batch_size), verbose=1)
            test_predictions = val_predictions
        else:
            val_gen = self.val_gen
            val_predictions = model.predict(val_gen, batch_size=batch_size,
                                                    steps=np.ceil(val_gen.n / batch_size), verbose=1)
            test_gen = self.test_gen
            test_predictions = model.predict(test_gen, batch_size=batch_size, steps=np.ceil(test_gen.n / batch_size), verbose=1)

        return val_predictions, test_predictions

    def calc_patch_level_metrics(self, predictions_softmax):
        predictions = np.argmax(predictions_softmax, axis=1)
        unlabeled_index = self.num_classes
        gt_classes = self.test_df['class']
        indices_of_labeled_patches = (gt_classes != str(unlabeled_index))
        gt_classes = np.array(gt_classes[indices_of_labeled_patches]).astype(np.int)
        predictions = np.array(predictions[indices_of_labeled_patches]).astype(np.int)

        metrics ={}
        metrics['accuracy'] = accuracy_score(gt_classes, predictions)
        metrics['cohens_quadratic_kappa'] = cohen_kappa_score(gt_classes, predictions, weights='quadratic')
        metrics['f1_mean'] = f1_score(gt_classes, predictions, average='macro')
        f1_score_classwise = f1_score(gt_classes, predictions, average=None)
        for class_id in range(len(f1_score_classwise)):
            key = 'f1_class_id_' + str(class_id)
            metrics[key] = f1_score_classwise[class_id]
        return metrics

    def calc_optimal_wsi_metrics(self, val_predictions, test_predictions):
        confidence_threshold = self.calc_optimal_confidence_threshold(val_predictions, self.val_df)
        metrics_dict, confusion_matrices, _ = self.calc_wsi_metrics(test_predictions, self.test_df, confidence_threshold)

        return metrics_dict, confusion_matrices

    def calc_wsi_metrics(self, predictions, gt_df, confidence_threshold):
        wsi_dataframe = self.data_gen.wsi_df
        wsi_predict_dataframe = self.get_predictions_per_wsi(predictions, gt_df, confidence_threshold)
        wsi_gt_dataframe = wsi_dataframe[wsi_dataframe['slide_id'].isin(wsi_predict_dataframe['slide_id'])]
        if self.dataset_type == 'prostate_cancer':
            metrics_dict, confusion_matrices, optimization_value = calc_wsi_prostate_cancer_metrics(wsi_predict_dataframe, wsi_gt_dataframe)

        return metrics_dict, confusion_matrices, optimization_value

    def calc_optimal_confidence_threshold(self, predictions, gt_dataframe):
        confidence_thresholds = np.arange(0.3, 1.0, 0.1)
        optimization_values = np.zeros_like(confidence_thresholds)
        for i in range(len(confidence_thresholds)):
            _, _, opt_value = self.calc_wsi_metrics(predictions, gt_dataframe, confidence_thresholds[i])
            optimization_values[i] = opt_value
        id_optimal_value = np.argmax(optimization_values)
        optimal_threshold = confidence_thresholds[id_optimal_value]
        return optimal_threshold

    def get_predictions_per_wsi(self, predictions_softmax, patch_dataframe, confidence_threshold):
        confidences = np.max(predictions_softmax, axis=1)
        predictions = np.argmax(predictions_softmax, axis=1)
        wsi_names = []
        wsi_primary = []
        wsi_secondary = []
        num_predictions_per_class = np.zeros(shape=predictions_softmax.shape[0])
        confidences_per_class = np.zeros(shape=predictions_softmax.shape[0])

        row = 0
        while True:
            wsi_name = patch_dataframe['wsi'][row]
            wsi_df = patch_dataframe[patch_dataframe['wsi'] == wsi_name]
            end_row_wsi = row + len(wsi_df)
            for class_id in range(len(num_predictions_per_class)):
                predictions_for_wsi = predictions[row:end_row_wsi]
                confidences_for_wsi = confidences[row:end_row_wsi]
                class_id_predicted = (predictions_for_wsi == class_id)
                top_5_confidences = np.argsort(confidences_for_wsi[class_id_predicted], axis=0)[0:5]
                top_5_conf_average = np.mean(top_5_confidences)

                class_id_predicted_with_confidence = confidences_for_wsi[class_id_predicted] > confidence_threshold
                num_predictions = np.count_nonzero(class_id_predicted_with_confidence)
                num_predictions_per_class[class_id] = num_predictions
                confidences_per_class[class_id] = top_5_conf_average

            if self.dataset_type == 'prostate_cancer':
                primary, secondary = calc_gleason_grade(num_predictions_per_class, confidences_per_class, confidence_threshold)

            wsi_names.append(wsi_name)
            wsi_primary.append(primary)
            wsi_secondary.append(secondary)
            if end_row_wsi == len(patch_dataframe):
                break
            else:
                row = end_row_wsi

        wsi_predict_dataframe = pd.DataFrame()
        wsi_predict_dataframe['slide_id'] = wsi_names
        wsi_predict_dataframe['Gleason_primary'] = wsi_primary
        wsi_predict_dataframe['Gleason_secondary'] = wsi_secondary

        return wsi_predict_dataframe

    def add_prefix(self, dict, prefix):
        new_dict = {}
        for key in dict.keys():
            new_key = prefix + '_' + key
            new_dict[new_key] = dict[key]
        return new_dict